"""Crop a video to a saved Step 09a region of interest."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

from .stitching_common import StitchingError, load_settings
from .workflow_paths import results_directory, scenario_prefix


class CropError(RuntimeError):
    """Actionable Step 09b input, encoding, or verification failure."""


def _resolve(value: str | Path, root: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _tools() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise CropError("FFmpeg and ffprobe must be installed and available on PATH for Step 09b.")
    return ffmpeg, ffprobe


def _probe(ffprobe: str, path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CropError(f"Video file does not exist: {path}")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError as exc:
        raise CropError(f"Could not start ffprobe for {path}: {exc}") from exc
    if result.returncode:
        raise CropError(f"Could not inspect {path}: {result.stderr.strip() or 'ffprobe failed'}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CropError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CropError(f"ffprobe returned invalid media information for {path}.")
    return payload


def _video_info(payload: dict[str, Any], path: Path) -> tuple[dict[str, Any], float, float, int, int]:
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise CropError(f"No media streams were found in {path}.")
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video is None:
        raise CropError(f"No video stream was found in {path}.")
    try:
        width, height = int(video["width"]), int(video["height"])
        raw_rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
        fps = float(Fraction(raw_rate))
        duration = float(video.get("duration") or payload.get("format", {}).get("duration"))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise CropError(f"Could not determine video dimensions, frame rate, and duration for {path}.") from exc
    if width <= 0 or height <= 0 or not math.isfinite(fps) or fps <= 0 or not math.isfinite(duration) or duration <= 0:
        raise CropError(f"Video has invalid dimensions, frame rate, or duration: {path}")
    return video, duration, fps, width, height


def _load_roi(path: Path, scenario: str, width: int, height: int, duration: float) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise CropError(f"ROI JSON file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CropError(f"ROI JSON is malformed ({path}): {exc}") from exc
    except OSError as exc:
        raise CropError(f"Could not read ROI JSON {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise CropError(f"ROI record must contain a JSON object: {path}")
    if record.get("ScenarioName") != scenario:
        raise CropError(
            f"ROI scenario {record.get('ScenarioName')!r} does not match settings scenario {scenario!r}."
        )
    try:
        frame_width_value = record["FrameWidth_px"]
        frame_height_value = record["FrameHeight_px"]
        frame_time = float(record["FrameTime_s"])
        region = record["RegionOfInterest"]
        top_left, bottom_right = region["TopLeft"], region["BottomRight"]
        values = (
            frame_width_value, frame_height_value, top_left["x"], top_left["y"],
            bottom_right["x"], bottom_right["y"], region["Width_px"], region["Height_px"],
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("ROI pixel dimensions and coordinates must be integers")
        frame_width, frame_height = frame_width_value, frame_height_value
        x, y = top_left["x"], top_left["y"]
        right, bottom = bottom_right["x"], bottom_right["y"]
        stored_width, stored_height = region["Width_px"], region["Height_px"]
    except (KeyError, TypeError, ValueError) as exc:
        raise CropError(f"ROI JSON is missing valid frame dimensions, timestamp, or corner coordinates: {path}") from exc
    if frame_width != width or frame_height != height:
        raise CropError(
            f"ROI was selected on a {frame_width}x{frame_height} frame, but source video is {width}x{height}. "
            "Select an ROI from this video's dimensions or choose a matching video."
        )
    if not math.isfinite(frame_time) or frame_time < 0 or frame_time >= duration:
        raise CropError(f"ROI frame timestamp must fall within the source video duration ({duration:.3f}s).")
    if x < 0 or y < 0 or right <= x or bottom <= y or right >= width or bottom >= height:
        raise CropError("ROI corners must define a non-empty rectangle inside the source frame.")
    actual_width, actual_height = right - x + 1, bottom - y + 1
    if (stored_width, stored_height) != (actual_width, actual_height):
        raise CropError(
            f"ROI stored dimensions {stored_width}x{stored_height} do not match its inclusive corner coordinates "
            f"({actual_width}x{actual_height})."
        )
    return {"x": x, "y": y, "width": actual_width, "height": actual_height, "frame_time": frame_time}


def _select_roi(
    settings: dict[str, Any], root: Path, explicit: Path | None,
    video_width: int, video_height: int, duration: float,
) -> tuple[Path, dict[str, Any]]:
    scenario = settings["ScenarioName"]
    folder = results_directory(settings, root) / "Step09a_IdentifyRegionOfInterest"
    if explicit is not None:
        path = _resolve(explicit, root)
        return path, _load_roi(path, scenario, video_width, video_height, duration)
    prefix = scenario_prefix(scenario)
    if not folder.is_dir():
        raise CropError(f"Step 09a ROI folder does not exist: {folder}. Run Step 09a first or pass --roi PATH.")
    candidates = sorted(folder.glob(f"{prefix}_RegionOfInterest_*.json"), key=lambda candidate: candidate.name, reverse=True)
    if not candidates:
        raise CropError(f"No ROI records for scenario {scenario!r} were found in {folder}. Run Step 09a first.")
    rejected: list[str] = []
    for path in candidates:
        try:
            return path.resolve(), _load_roi(path, scenario, video_width, video_height, duration)
        except CropError as exc:
            rejected.append(f"{path.name}: {exc}")
    details = "\n  ".join(rejected[:5])
    raise CropError(f"No valid matching ROI record was found in {folder}.\n  {details}")


def _positive_seconds(value: float | None, duration: float) -> float:
    if value is None:
        return duration
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise CropError("--preview-seconds must be a positive finite number.") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise CropError("--preview-seconds must be a positive finite number.")
    return min(seconds, duration)


def _encoder_args(video: dict[str, Any]) -> list[str]:
    codec = video.get("codec_name")
    encoder = {
        "h264": "libx264", "hevc": "libx265", "mpeg4": "mpeg4",
        "vp9": "libvpx-vp9", "av1": "libaom-av1",
    }.get(codec)
    if not encoder:
        raise CropError(f"Source video codec {codec!r} is not supported for ROI crop encoding.")
    args = ["-c:v", encoder]
    profile = video.get("profile")
    if codec == "h264":
        args.extend(["-preset", "medium"])
        # Step 08b produces H.264 using CRF 18. Use that quality target when
        # the source does not carry a declared target bitrate.
        try:
            bitrate = int(video.get("bit_rate"))
        except (TypeError, ValueError):
            bitrate = 0
        if bitrate > 0:
            args.extend(["-b:v", str(bitrate)])
        else:
            args.extend(["-crf", "18"])
        if profile and str(profile).lower() in {"baseline", "constrained baseline", "main", "high"}:
            profile_name = "baseline" if str(profile).lower() == "constrained baseline" else str(profile).lower()
            args.extend(["-profile:v", profile_name])
    elif codec == "hevc":
        args.extend(["-preset", "medium", "-crf", "28"])
    elif codec == "mpeg4":
        try:
            bitrate = int(video.get("bit_rate"))
        except (TypeError, ValueError):
            bitrate = 0
        args.extend(["-q:v", "3"] if bitrate <= 0 else ["-b:v", str(bitrate)])
    elif codec == "vp9":
        args.extend(["-deadline", "good", "-crf", "32", "-b:v", "0"])
    else:
        args.extend(["-crf", "30", "-b:v", "0"])
    pixel_format = video.get("pix_fmt")
    if pixel_format:
        args.extend(["-pix_fmt", str(pixel_format)])
    return args


def _encode(
    ffmpeg: str, ffprobe: str, source: Path, temporary: Path, video: dict[str, Any],
    source_payload: dict[str, Any], roi: dict[str, Any], duration: float,
) -> None:
    audio_streams = [stream for stream in source_payload.get("streams", []) if stream.get("codec_type") == "audio"]
    crop = f"crop={roi['width']}:{roi['height']}:{roi['x']}:{roi['y']}:exact=1"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-progress", "pipe:1", "-nostats", "-i", str(source), "-t", f"{duration:.9f}",
        "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0", "-vf", crop,
        "-fps_mode", "passthrough", *_encoder_args(video),
    ]
    if audio_streams:
        command.extend(["-c:a", "copy"])
    command.extend(["-movflags", "+faststart", str(temporary)])

    started = time.monotonic()
    last_percent = -1
    try:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as errors:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=errors, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                key, separator, value = line.strip().partition("=")
                if not separator or key not in {"out_time_us", "out_time_ms"}:
                    continue
                try:
                    output_time = int(value) / 1_000_000
                except ValueError:
                    continue
                percent = min(99, max(0, int(output_time * 100 / duration)))
                if percent != last_percent:
                    elapsed = time.monotonic() - started
                    eta = elapsed * (duration / output_time - 1) if output_time > 0 else None
                    eta_text = f" | ETA {eta:.0f}s" if eta is not None and math.isfinite(eta) else ""
                    print(
                        f"\rStep 09b progress: {percent:3d}% | {output_time:.1f}/{duration:.1f}s "
                        f"| elapsed {elapsed:.0f}s{eta_text}", end="", flush=True,
                    )
                    last_percent = percent
            code = process.wait()
            errors.seek(0)
            detail = errors.read().strip()
    except OSError as exc:
        raise CropError(f"Could not run FFmpeg for {source}: {exc}") from exc
    if last_percent >= 0:
        print("\rStep 09b progress: 100%", flush=True)
    if code:
        raise CropError(f"FFmpeg failed while cropping {source} (exit {code}): {detail or 'no diagnostic provided'}")
    if not temporary.is_file() or temporary.stat().st_size <= 0:
        raise CropError("FFmpeg did not create a non-empty cropped video.")

    output_payload = _probe(ffprobe, temporary)
    output_video, output_duration, output_fps, output_width, output_height = _video_info(output_payload, temporary)
    expected_size = (roi["width"], roi["height"])
    if (output_width, output_height) != expected_size:
        raise CropError(f"Output dimensions are {output_width}x{output_height}; expected {expected_size[0]}x{expected_size[1]}.")
    source_fps = float(Fraction(video.get("avg_frame_rate") or video.get("r_frame_rate")))
    if abs(output_fps - source_fps) > max(0.001, source_fps * 1e-4):
        raise CropError(f"Output frame rate is {output_fps:g} fps; expected {source_fps:g} fps.")
    if len([s for s in output_payload.get("streams", []) if s.get("codec_type") == "audio"]) != len(audio_streams):
        raise CropError("Output audio-stream count does not match the source video.")
    output_audio = [s for s in output_payload.get("streams", []) if s.get("codec_type") == "audio"]
    for index, (source_audio, actual_audio) in enumerate(zip(audio_streams, output_audio), start=1):
        for field in ("codec_name", "sample_rate", "channels"):
            expected, actual = source_audio.get(field), actual_audio.get(field)
            if expected is not None and actual != expected:
                raise CropError(
                    f"Output audio stream {index} has {field}={actual!r}; source has {expected!r}."
                )
    tolerance = max(1.0 / source_fps, 0.02)
    if abs(output_duration - duration) > tolerance:
        raise CropError(f"Output duration {output_duration:.3f}s differs from expected {duration:.3f}s by more than {tolerance:.3f}s.")
    if output_video.get("codec_name") != video.get("codec_name"):
        raise CropError(f"Output codec is {output_video.get('codec_name')!r}; expected source codec {video.get('codec_name')!r}.")
    if video.get("profile") and output_video.get("profile") != video.get("profile"):
        raise CropError(
            f"Output codec profile is {output_video.get('profile')!r}; expected source profile {video.get('profile')!r}."
        )
    if video.get("pix_fmt") and output_video.get("pix_fmt") != video.get("pix_fmt"):
        raise CropError(f"Output pixel format is {output_video.get('pix_fmt')}; expected source format {video.get('pix_fmt')}.")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crop a Step 08b video to a saved Step 09a ROI.")
    parser.add_argument("--settings", type=Path, help="Settings JSON; relative paths resolve from repository root.")
    parser.add_argument("--video", type=Path, help="Source video; defaults to the configured Step 08b output.")
    parser.add_argument("--roi", type=Path, help="ROI JSON; defaults to the newest valid scenario record.")
    parser.add_argument("--preview-seconds", type=float, help="Encode only the first N seconds for review.")
    return parser.parse_args(argv)


def main(repository_root: Path | None = None, argv: Sequence[str] | None = None) -> int:
    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    args = _parse_args(argv)
    temporary: Path | None = None
    try:
        settings = load_settings(root, args.settings)
        ffmpeg, ffprobe = _tools()
        scenario_value = settings["ScenarioName"]
        scenario = scenario_prefix(scenario_value)
        source = _resolve(args.video, root) if args.video else (
            results_directory(settings, root) / "Step08b_RemovedSections" / f"{scenario}_SectionsRemoved.mp4"
        ).resolve()
        source_payload = _probe(ffprobe, source)
        video, source_duration, source_fps, width, height = _video_info(source_payload, source)
        roi_path, roi = _select_roi(settings, root, args.roi, width, height, source_duration)
        duration = _positive_seconds(args.preview_seconds, source_duration)
        preview = args.preview_seconds is not None

        output_dir = results_directory(settings, root) / "Step09b_CropToRegionOfInterest"
        output_dir.mkdir(parents=True, exist_ok=True)
        if preview:
            duration_label = f"{duration:g}".replace(".", "p")
            output = output_dir / f"{scenario}_CroppedToRegionOfInterest_Preview_{duration_label}s.mp4"
        else:
            output = output_dir / f"{scenario}_CroppedToRegionOfInterest.mp4"
        if output.resolve() == source.resolve():
            raise CropError("The source and output paths resolve to the same file; refusing to overwrite the source.")
        with tempfile.NamedTemporaryFile(prefix=".step09b_", suffix=output.suffix, dir=output_dir, delete=False) as handle:
            temporary = Path(handle.name)

        print(f"Source video: {source}")
        print(f"ROI record: {roi_path}")
        print(f"Crop: x={roi['x']}, y={roi['y']}, {roi['width']}x{roi['height']} pixels")
        print(f"Processing: {'preview' if preview else 'full video'} ({duration:.3f}s at {source_fps:g} fps)")
        print("Audio: present; source audio streams will be copied" if any(s.get("codec_type") == "audio" for s in source_payload.get("streams", [])) else "Audio: absent; output will be silent")
        _encode(ffmpeg, ffprobe, source, temporary, video, source_payload, roi, duration)
        os.replace(temporary, output)
        temporary = None
        print(f"Cropped video saved to: {output.resolve()}")
        return 0
    except (StitchingError, CropError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Step 09b failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
