"""Timestamp-align, warp, composite, and encode the Step 07 video pair."""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

from .stitching_common import StitchingError, load_coordinate_data, load_settings
from .workflow_paths import VIDEO_EXTENSIONS, results_directory, scenario_prefix


@dataclass
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    timestamps: list[float]
    duration: float
    has_audio: bool


def _run_probe(ffprobe: str, args: list[str], path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", *args, "-of", "json", str(path)],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise StitchingError(f"Could not probe video {path}: {detail.strip()}") from exc


def _fraction(value: str) -> float:
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _probe_video(ffprobe: str, path: Path) -> VideoInfo:
    if not path.is_file():
        raise StitchingError(f"Video file does not exist: {path}")
    data = _run_probe(ffprobe, ["-show_streams", "-show_format"], path)
    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video is None:
        raise StitchingError(f"No video stream was found in {path}")
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    fps = _fraction(video.get("avg_frame_rate", "0/0")) or _fraction(video.get("r_frame_rate", "0/0"))
    if width <= 0 or height <= 0 or not math.isfinite(fps) or fps <= 0:
        raise StitchingError(f"Invalid dimensions or frame rate in {path}")
    frames_data = _run_probe(
        ffprobe,
        ["-select_streams", "v:0", "-show_frames", "-show_entries", "frame=best_effort_timestamp_time,pts_time"],
        path,
    )
    timestamps: list[float] = []
    for frame in frames_data.get("frames", []):
        raw = frame.get("best_effort_timestamp_time", frame.get("pts_time"))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            timestamps.append(value)
    if not timestamps:
        raise StitchingError(f"No usable presentation timestamps were found in {path}")
    if any(b < a for a, b in zip(timestamps, timestamps[1:])):
        raise StitchingError(f"Video presentation timestamps are not ordered in {path}")
    try:
        stream_start = float(video.get("start_time"))
    except (TypeError, ValueError):
        stream_start = timestamps[0]
    if not math.isfinite(stream_start):
        stream_start = timestamps[0]
    timestamps = [value - stream_start for value in timestamps]
    duration_value = data.get("format", {}).get("duration")
    try:
        duration = float(duration_value)
    except (TypeError, ValueError):
        duration = timestamps[-1] + 1.0 / fps
    if not math.isfinite(duration) or duration <= 0:
        duration = timestamps[-1] + 1.0 / fps
    return VideoInfo(
        path, width, height, fps, timestamps, duration,
        any(stream.get("codec_type") == "audio" for stream in streams),
    )


def _canonical_videos(settings: dict[str, Any], root: Path) -> tuple[Path, Path]:
    folder = results_directory(settings, root) / "Step02_AlignedVideos"
    prefix = scenario_prefix(settings["ScenarioName"])
    paths: list[Path] = []
    for camera in ("CameraA", "CameraB"):
        candidates = sorted(
            path for path in folder.glob(f"{prefix}_{camera}_Aligned.*")
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
        if len(candidates) != 1:
            raise StitchingError(
                f"Expected exactly one canonical aligned {camera} video in {folder}, found {len(candidates)}. "
                "Use --videos LEFT RIGHT to choose another pair."
            )
        paths.append(candidates[0].resolve())
    return paths[0], paths[1]


def _resolve_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _load_homography(settings: dict[str, Any], root: Path, requested: str | None) -> tuple[np.ndarray, Path, Path]:
    if requested:
        artifact_path = _resolve_path(requested, root)
    else:
        artifact_path = (
            results_directory(settings, root) / "Step05_Homography"
            / f"{scenario_prefix(settings['ScenarioName'])}_Homography.json"
        )
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StitchingError(f"Could not read valid homography JSON at {artifact_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StitchingError(f"Homography artifact must contain a JSON object: {artifact_path}")
    if payload.get("ScenarioName") != settings["ScenarioName"]:
        raise StitchingError(
            f"Homography scenario {payload.get('ScenarioName')!r} does not match configured scenario "
            f"{settings['ScenarioName']!r}."
        )
    try:
        matrix = np.asarray(payload.get("HomographyMatrix"), dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise StitchingError("HomographyMatrix must be a finite invertible 3x3 matrix.") from exc
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or np.linalg.matrix_rank(matrix) < 3:
        raise StitchingError("HomographyMatrix must be a finite invertible 3x3 matrix.")
    coordinate_text = payload.get("CoordinateRecordPath")
    if not isinstance(coordinate_text, str) or not coordinate_text.strip():
        raise StitchingError("Homography artifact is missing CoordinateRecordPath.")
    coordinates_path = _resolve_path(coordinate_text, root)
    if not coordinates_path.is_file():
        raise StitchingError(f"Coordinate record referenced by the homography is missing: {coordinates_path}")
    return matrix, coordinates_path, artifact_path


def _render_setup(
    left_width: int, left_height: int, right_width: int, right_height: int,
    left_points: np.ndarray, right_points: np.ndarray, homography: np.ndarray,
) -> tuple[np.ndarray, tuple[int, int], tuple[int, int, int, int]]:
    right_corners = np.float32(
        [[0, 0], [right_width, 0], [right_width, right_height], [0, right_height]]
    ).reshape(-1, 1, 2)
    denoms = np.c_[right_corners.reshape(-1, 2), np.ones(4)] @ homography[2, :]
    transform = homography
    if denoms.min() <= 0 <= denoms.max():
        affine, inliers = cv2.estimateAffinePartial2D(
            right_points, left_points, method=cv2.RANSAC, ransacReprojThreshold=5.0,
        )
        count = int(inliers.sum()) if inliers is not None else 0
        if affine is None or count < 4:
            raise StitchingError(
                "The homography horizon crosses the right frame and fewer than four reliable landmark pairs "
                "support the Step 06 similarity-transform fallback."
            )
        transform = np.vstack((affine, [0.0, 0.0, 1.0]))
        print(f"Homography horizon crosses the right frame; using Step 06 similarity fallback ({count} inliers).")
    projected = cv2.perspectiveTransform(right_corners, transform).reshape(-1, 2)
    if not np.isfinite(projected).all():
        raise StitchingError("Transform projected non-finite right-image corners.")
    min_x, min_y = min(0, int(np.floor(projected[:, 0].min()))), min(0, int(np.floor(projected[:, 1].min())))
    max_x, max_y = max(left_width, int(np.ceil(projected[:, 0].max()))), max(left_height, int(np.ceil(projected[:, 1].max())))
    width, height = max_x - min_x, max_y - min_y
    if width <= 0 or height <= 0 or width * height > 250_000_000:
        raise StitchingError(f"Estimated panorama dimensions are unreasonable ({width}x{height}).")
    # H.264 yuv420p requires even frame dimensions; add at most one border row/column.
    width += width % 2
    height += height % 2
    panorama = np.array([[1, 0, -min_x], [0, 1, -min_y], [0, 0, 1]], dtype=np.float64) @ transform
    return panorama, (width, height), (-min_x, -min_y, left_width, left_height)


def _composite(
    left: np.ndarray, right: np.ndarray, transform: np.ndarray, size: tuple[int, int],
    left_rect: tuple[int, int, int, int], overlap_mode: str,
) -> np.ndarray:
    width, height = size
    warped = cv2.warpPerspective(right, transform, size)
    mask = cv2.warpPerspective(
        np.full(right.shape[:2], 255, dtype=np.uint8), transform, size, flags=cv2.INTER_NEAREST,
    ) > 0
    x, y, left_width, left_height = left_rect
    left_layer = np.zeros((height, width, 3), dtype=np.uint8)
    left_layer[y:y + left_height, x:x + left_width] = left
    left_mask = np.zeros((height, width), dtype=bool)
    left_mask[y:y + left_height, x:x + left_width] = True
    result = left_layer.copy()
    result[mask & ~left_mask] = warped[mask & ~left_mask]
    if overlap_mode == "blend":
        overlap = mask & left_mask
        result[overlap] = cv2.addWeighted(left_layer[overlap], 0.5, warped[overlap], 0.5, 0)
    return result


def _next_frame(capture: cv2.VideoCapture, path: Path, frame_number: int) -> np.ndarray:
    ok, frame = capture.read()
    if not ok or frame is None:
        raise StitchingError(f"Could not decode frame {frame_number} from {path}")
    return frame


def _ffmpeg_tools() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise StitchingError("FFmpeg and ffprobe must be installed and available on PATH for Step 07.")
    return ffmpeg, ffprobe


def _encode(
    ffmpeg: str, ffprobe: str, left_info: VideoInfo, right_info: VideoInfo,
    transform: np.ndarray, size: tuple[int, int], left_rect: tuple[int, int, int, int],
    overlap_mode: str, output_path: Path,
) -> None:
    width, height = size
    fps = left_info.fps
    common_duration = min(left_info.duration, right_info.duration)
    expected_count = int(math.ceil(common_duration * fps - 1e-9))
    if expected_count <= 0:
        raise StitchingError("No Camera A frames fall inside the common input duration.")
    output_times = [index / fps for index in range(expected_count)]
    left_indices: list[int] = []
    for timestamp in output_times:
        nearest = bisect.bisect_left(left_info.timestamps, timestamp)
        candidates = [index for index in (nearest - 1, nearest) if 0 <= index < len(left_info.timestamps)]
        if not candidates:
            raise StitchingError(f"No Camera A frame is available near output timestamp {timestamp:.3f}s.")
        frame_index = min(candidates, key=lambda index: abs(left_info.timestamps[index] - timestamp))
        if abs(left_info.timestamps[frame_index] - timestamp) > max(1.5 / fps, 0.08):
            raise StitchingError(
                f"No suitably timestamp-aligned Camera A frame near {timestamp:.3f}s; "
                "the video may contain a timestamp gap."
            )
        left_indices.append(frame_index)
    timestamp_to_frame = right_info.timestamps
    cap_left, cap_right = cv2.VideoCapture(str(left_info.path)), cv2.VideoCapture(str(right_info.path))
    if not cap_left.isOpened() or not cap_right.isOpened():
        cap_left.release(); cap_right.release()
        raise StitchingError("OpenCV could not open one or both video decoders.")
    # OpenCV sequential decoding follows presentation order; ffprobe supplies the PTS for pairing.
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s:v", f"{width}x{height}", "-r", f"{fps:.12g}", "-i", "pipe:0",
    ]
    if left_info.has_audio:
        command.extend(["-i", str(left_info.path), "-map", "0:v:0", "-map", "1:a:0?"])
    command.extend([
        "-frames:v", str(expected_count), "-t", f"{common_duration:.9f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
    ])
    if left_info.has_audio:
        command.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-af", f"atrim=duration={common_duration:.9f},asetpts=PTS-STARTPTS"])
    command.extend(["-movflags", "+faststart", str(output_path)])
    log_path = output_path.with_suffix(".ffmpeg.log")
    started = time.monotonic()
    right_index = 0
    right_current = right_next = None
    decoded_right = 0
    last_left_index = -1
    cached_left_frame: np.ndarray | None = None
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log)
            try:
                # Seed the right-side bracket at its first two frames.
                right_current = _next_frame(cap_right, right_info.path, 0); decoded_right = 1
                if len(timestamp_to_frame) > 1:
                    right_next = _next_frame(cap_right, right_info.path, 1); decoded_right = 2
                last_report = 0
                for output_number, (source_index, source_timestamp) in enumerate(zip(left_indices, output_times), start=1):
                    while decoded_right < len(timestamp_to_frame) and right_next is None:
                        right_next = _next_frame(cap_right, right_info.path, decoded_right); decoded_right += 1
                    while right_next is not None and timestamp_to_frame[right_index + 1] <= source_timestamp:
                        right_current = right_next
                        right_index += 1
                        if decoded_right < len(timestamp_to_frame):
                            right_next = _next_frame(cap_right, right_info.path, decoded_right); decoded_right += 1
                        else:
                            right_next = None
                    # Choose the nearest right PTS. A one-frame cadence tolerance avoids pairing across gaps.
                    chosen = right_current
                    chosen_timestamp = timestamp_to_frame[right_index]
                    if right_next is not None and abs(timestamp_to_frame[right_index + 1] - source_timestamp) < abs(chosen_timestamp - source_timestamp):
                        chosen = right_next
                        chosen_timestamp = timestamp_to_frame[right_index + 1]
                    if abs(chosen_timestamp - source_timestamp) > max(1.5 / right_info.fps, 0.08):
                        raise StitchingError(
                            f"No suitably timestamp-aligned Camera B frame near {source_timestamp:.3f}s; "
                            "the videos may have a timestamp gap or different synchronization."
                        )
                    # Advance Camera A's sequential decoder to the frame nearest this output PTS.
                    left_frame = cached_left_frame if source_index == last_left_index else None
                    for _ in range(source_index - last_left_index):
                        left_frame = _next_frame(cap_left, left_info.path, source_index)
                    if source_index != last_left_index:
                        last_left_index = source_index
                        cached_left_frame = left_frame
                    if left_frame is None:
                        raise StitchingError("Internal Camera A frame sequencing error.")
                    if process.stdin is None:
                        raise StitchingError("FFmpeg input pipe was not available.")
                    composite = _composite(left_frame, chosen, transform, size, left_rect, overlap_mode)
                    process.stdin.write(composite.tobytes())
                    if output_number == 1 or output_number % max(1, int(fps * 5)) == 0 or output_number == expected_count:
                        elapsed = time.monotonic() - started
                        percent = output_number * 100 / expected_count
                        print(f"Step 07: {output_number}/{expected_count} frames ({percent:.1f}%), {elapsed:.1f}s elapsed", flush=True)
                        last_report = output_number
                process.stdin.close()
                return_code = process.wait()
                if return_code != 0:
                    raise StitchingError(f"FFmpeg encoding failed (exit {return_code}); diagnostics: {log_path}")
            except Exception as exc:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if isinstance(exc, BrokenPipeError):
                    try:
                        diagnostics = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                    except OSError:
                        diagnostics = "(FFmpeg log could not be read.)"
                    raise StitchingError(
                        f"FFmpeg stopped accepting video frames; diagnostics: {log_path}\n{diagnostics}"
                    ) from exc
                raise
    finally:
        cap_left.release(); cap_right.release()
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise StitchingError(f"FFmpeg did not create a valid output file: {output_path}; diagnostics: {log_path}")
    verified = _run_probe(ffprobe, ["-show_streams", "-show_format"], output_path)
    streams = verified.get("streams", [])
    out_video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    out_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    if (
        out_video is None or int(out_video.get("width", 0)) != width or int(out_video.get("height", 0)) != height
        or not math.isclose(_fraction(out_video.get("avg_frame_rate", "0/0")), fps, rel_tol=1e-4)
        or out_audio != left_info.has_audio
    ):
        raise StitchingError(f"Encoded output failed dimensions, frame-rate, or audio verification: {output_path}")
    try:
        duration = float(verified.get("format", {}).get("duration", "nan"))
    except (TypeError, ValueError):
        duration = float("nan")
    expected_duration = expected_count / fps
    if not math.isfinite(duration) or abs(duration - expected_duration) > max(1.0 / fps, 0.1):
        raise StitchingError(
            f"Encoded output duration {duration!r}s differs from expected {expected_duration:.3f}s."
        )
    log_path.unlink(missing_ok=True)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stitch synchronized aligned videos with the Step 05 homography.")
    parser.add_argument("--videos", nargs=2, metavar=("LEFT", "RIGHT"), help="Explicit left/Camera A and right/Camera B videos")
    parser.add_argument("--homography", help="Step 05 homography JSON (relative paths use the repository root)")
    parser.add_argument("--overlap-mode", choices=("left", "blend"), default="left")
    return parser.parse_args(argv)


def main(repository_root: Path | None = None, argv: Sequence[str] | None = None) -> int:
    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    args = _parse_args(argv)
    temporary_output: Path | None = None
    try:
        settings = load_settings(root)
        ffmpeg, ffprobe = _ffmpeg_tools()
        if args.videos:
            left_path, right_path = (_resolve_path(value, root) for value in args.videos)
        else:
            left_path, right_path = _canonical_videos(settings, root)
        homography, coordinate_path, artifact_path = _load_homography(settings, root, args.homography)
        coordinates = load_coordinate_data(coordinate_path, settings, root)
        left_info, right_info = _probe_video(ffprobe, left_path), _probe_video(ffprobe, right_path)
        expected = (
            (coordinates.left_width, coordinates.left_height),
            (coordinates.right_width, coordinates.right_height),
        )
        actual = ((left_info.width, left_info.height), (right_info.width, right_info.height))
        if actual != expected:
            raise StitchingError(
                f"Video dimensions do not match the homography coordinate record. Expected left/right "
                f"{expected[0]} / {expected[1]}, found {actual[0]} / {actual[1]}."
            )
        print(f"Camera A: {left_path}")
        print(f"Camera B: {right_path}")
        print(f"Homography: {artifact_path}")
        print(f"Overlap mode: {args.overlap_mode}; output FPS: {left_info.fps:.6g}")
        print("Camera A audio: present" if left_info.has_audio else "Camera A audio: absent; output will be silent.")
        transform, canvas_size, left_rect = _render_setup(
            left_info.width, left_info.height, right_info.width, right_info.height,
            coordinates.left_points, coordinates.right_points, homography,
        )
        print(f"Panorama dimensions: {canvas_size[0]}x{canvas_size[1]}; common duration: {min(left_info.duration, right_info.duration):.3f}s")
        output_dir = results_directory(settings, root) / "Step07_StitchedVideos"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{scenario_prefix(settings['ScenarioName'])}_Stitched.mp4"
        with tempfile.NamedTemporaryFile(prefix=".step07_", suffix=".mp4", dir=output_dir, delete=False) as temporary:
            temporary_output = Path(temporary.name)
        _encode(
            ffmpeg, ffprobe, left_info, right_info, transform, canvas_size, left_rect,
            args.overlap_mode, temporary_output,
        )
        os.replace(temporary_output, output_path)
        temporary_output = None
        print(f"Stitched video saved to: {output_path.resolve()}")
        return 0
    except (StitchingError, OSError, ValueError, cv2.error, subprocess.SubprocessError) as exc:
        print(f"Step 07 failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary_output is not None:
            temporary_output.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
