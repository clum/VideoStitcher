"""Extract fixed-time still images from the Step 02 aligned video pair."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

from .workflow_paths import results_directory, scenario_prefix


TIMESTAMPS_S = (0, 25, 50, 75, 100)
IMAGE_FORMATS = {".jpg": "mjpeg", ".png": "png", ".bmp": "bmp"}


class Step03Error(RuntimeError):
    """An actionable input, extraction, or publishing error."""


@dataclass(frozen=True)
class VideoSource:
    camera: str
    path: Path
    width: int
    height: int
    duration: Fraction
    start_time: Fraction


@dataclass(frozen=True)
class FrameSelection:
    source: VideoSource
    requested_time_s: int
    presentation_time: Fraction


def _required_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    missing = [name for name, value in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not value]
    if missing:
        raise Step03Error(
            "Required media tools were not found on PATH: " + ", ".join(missing)
        )
    return ffmpeg, ffprobe


def _run(command: list[str], description: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "No diagnostic output was produced."
        raise Step03Error(f"{description} failed (exit code {result.returncode}): {detail}")
    return result


def _fraction(value: Any, default: Fraction | None = None) -> Fraction:
    if value in (None, "", "N/A"):
        if default is not None:
            return default
        raise ValueError("missing timestamp")
    return Fraction(str(value))


def _probe_source(ffprobe: str, camera: str, path: Path) -> VideoSource:
    if not path.is_file():
        raise Step03Error(f"{camera} aligned video was not found: {path}")
    result = _run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,width,height,duration,start_time",
            "-of", "json",
            str(path),
        ],
        f"Probing {camera} input {path}",
    )
    try:
        payload = json.loads(result.stdout)
        video = next(stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video")
        duration_value = video.get("duration") or payload.get("format", {}).get("duration")
        duration = _fraction(duration_value)
        start_time = _fraction(video.get("start_time"), Fraction(0))
        width, height = int(video["width"]), int(video["height"])
        if duration <= 0 or width <= 0 or height <= 0:
            raise ValueError("invalid duration or dimensions")
    except (ValueError, TypeError, KeyError, StopIteration, ZeroDivisionError) as exc:
        raise Step03Error(f"Could not determine {camera} video duration or dimensions: {path}") from exc
    return VideoSource(camera, path, width, height, duration, start_time)


def _resolve_video_override(path: Path | None, repository_root: Path) -> Path | None:
    if path is None:
        return None
    expanded = path.expanduser()
    return (expanded if expanded.is_absolute() else repository_root / expanded).resolve()


def _timestamp_text(value: Fraction) -> str:
    return f"{float(value):.9f}"


def _nearest_frame_time(ffprobe: str, source: VideoSource, requested_time_s: int) -> Fraction:
    target = source.start_time + requested_time_s
    # Inspect a local window around the target. This includes frames on both
    # sides so the selected frame is the closest decoded presentation time.
    window_start = max(source.start_time, target - 2)
    window_duration = max(Fraction(1, 10), target + 2 - window_start)
    interval = f"{_timestamp_text(window_start)}%+{_timestamp_text(window_duration)}"
    result = _run(
        [
            ffprobe,
            "-v", "error",
            "-select_streams", "v:0",
            "-read_intervals", interval,
            "-show_frames",
            "-show_entries", "frame=best_effort_timestamp_time",
            "-of", "json",
            str(source.path),
        ],
        f"Finding a frame for {source.camera} at {requested_time_s} seconds",
    )
    try:
        frames = json.loads(result.stdout).get("frames", [])
        timestamps = [
            _fraction(frame.get("best_effort_timestamp_time"))
            for frame in frames
            if frame.get("best_effort_timestamp_time") not in (None, "N/A")
            and abs(_fraction(frame.get("best_effort_timestamp_time")) - target) <= 2
        ]
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise Step03Error(
            f"Could not read frame timestamps from {source.camera} at {requested_time_s} seconds: {source.path}"
        ) from exc
    if not timestamps:
        raise Step03Error(
            f"No decodable frame was found in {source.camera} near {requested_time_s} seconds: {source.path}"
        )
    return min(timestamps, key=lambda timestamp: abs(timestamp - target))


def _extract_frame(
    ffmpeg: str,
    selection: FrameSelection,
    frame_path: Path,
) -> None:
    source_time = selection.presentation_time - selection.source.start_time
    command = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", _timestamp_text(source_time),
        "-i", str(selection.source.path),
        "-map", "0:v:0", "-frames:v", "1", "-an",
        "-update", "1", str(frame_path),
    ]
    _run(
        command,
        f"Extracting {selection.source.camera} frame at {selection.requested_time_s} seconds",
    )
    if not frame_path.is_file() or frame_path.stat().st_size == 0:
        raise Step03Error(
            f"No image was extracted from {selection.source.camera} at {selection.requested_time_s} seconds."
        )


def _encode_variant(
    ffmpeg: str,
    frame_path: Path,
    output_path: Path,
) -> None:
    command = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(frame_path),
        "-map", "0:v:0", "-frames:v", "1", "-an", "-update", "1",
    ]
    if output_path.suffix.lower() == ".jpg":
        command += ["-q:v", "2"]
    command.append(str(output_path))
    _run(command, f"Writing image {output_path.name}")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise Step03Error(f"Image output was not created: {output_path}")


def _validate_image(
    ffprobe: str,
    image_path: Path,
    source: VideoSource,
) -> None:
    result = _run(
        [
            ffprobe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height",
            "-of", "json",
            str(image_path),
        ],
        f"Validating image {image_path.name}",
    )
    try:
        streams = json.loads(result.stdout).get("streams", [])
        image = streams[0]
        codec = IMAGE_FORMATS[image_path.suffix.lower()]
        dimensions = (int(image["width"]), int(image["height"]))
        if image.get("codec_name") != codec:
            raise ValueError(f"expected {codec}, found {image.get('codec_name')}")
        if dimensions != (source.width, source.height):
            raise ValueError(
                f"expected {source.width}x{source.height}, found {dimensions[0]}x{dimensions[1]}"
            )
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise Step03Error(f"Image validation failed for {image_path}: {exc}") from exc


def _publish_set(staged_images: Path, output_dir: Path, filenames: list[str], backup_dir: Path) -> None:
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for filename in filenames:
            destination = output_dir / filename
            if destination.exists():
                backup = backup_dir / filename
                destination.replace(backup)
                backups[destination] = backup
        for filename in filenames:
            destination = output_dir / filename
            (staged_images / filename).replace(destination)
            published.append(destination)
    except OSError as exc:
        rollback_errors: list[str] = []
        for destination in published:
            try:
                destination.unlink(missing_ok=True)
            except OSError as rollback_exc:
                rollback_errors.append(f"Could not remove partial output {destination}: {rollback_exc}")
        for destination, backup in backups.items():
            try:
                if backup.exists():
                    backup.replace(destination)
            except OSError as rollback_exc:
                rollback_errors.append(f"Could not restore previous output {destination}: {rollback_exc}")
        details = " " + " ".join(rollback_errors) if rollback_errors else ""
        raise Step03Error(f"Could not publish the complete Step 03 image set: {exc}.{details}") from exc


def extract_aligned_images(
    settings: dict[str, Any],
    repository_root: Path,
    camera_a_path: Path | None = None,
    camera_b_path: Path | None = None,
) -> list[Path]:
    """Extract and transactionally publish all 30 Step 03 images."""
    ffmpeg, ffprobe = _required_tools()
    scenario = settings.get("ScenarioName")
    if not isinstance(scenario, str) or not scenario.strip():
        raise Step03Error("Set a non-empty ScenarioName in settings.json.")
    prefix = scenario_prefix(scenario.strip())
    output_dir = results_directory(settings, repository_root) / "Step03_ExtractedImages"
    source_dir = results_directory(settings, repository_root) / "Step02_AlignedVideos"
    requested_paths = {
        "CameraA": _resolve_video_override(camera_a_path, repository_root),
        "CameraB": _resolve_video_override(camera_b_path, repository_root),
    }
    sources = []
    for camera in ("CameraA", "CameraB"):
        path = requested_paths[camera] or source_dir / f"{prefix}_{camera}_Aligned.mp4"
        source = _probe_source(ffprobe, camera, path)
        sources.append(source)
        print(f"{camera} input video: {source.path}")

    selections: list[FrameSelection] = []
    for source in sources:
        for requested_time_s in TIMESTAMPS_S:
            if source.duration < requested_time_s:
                raise Step03Error(
                    f"{source.camera} video is too short for the {requested_time_s}-second frame "
                    f"({float(source.duration):.6f}s): {source.path}"
                )
            presentation_time = _nearest_frame_time(ffprobe, source, requested_time_s)
            selections.append(FrameSelection(source, requested_time_s, presentation_time))

    output_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = output_dir / f".step03_{uuid.uuid4().hex}"
    staged_images = stage_dir / "images"
    staged_frames = stage_dir / "frames"
    backup_dir = stage_dir / "backups"
    staged_images.mkdir(parents=True)
    staged_frames.mkdir()
    backup_dir.mkdir()

    filenames: list[str] = []
    try:
        for index, selection in enumerate(selections, start=1):
            camera = selection.source.camera
            stamp = selection.requested_time_s
            print(f"[{index}/10] Extracting {camera} at {stamp} seconds...")
            frame_path = staged_frames / f"{camera}_t{stamp:03d}s.png"
            _extract_frame(ffmpeg, selection, frame_path)
            for extension in IMAGE_FORMATS:
                filename = f"{prefix}_{camera}_t{stamp:03d}s{extension}"
                _encode_variant(ffmpeg, frame_path, staged_images / filename)
                _validate_image(ffprobe, staged_images / filename, selection.source)
                filenames.append(filename)

        expected = len(sources) * len(TIMESTAMPS_S) * len(IMAGE_FORMATS)
        actual_staged = [path for path in staged_images.iterdir() if path.is_file()]
        if len(filenames) != expected or len(actual_staged) != expected:
            raise Step03Error(f"Expected {expected} staged images, found {len(actual_staged)}.")

        _publish_set(staged_images, output_dir, filenames, backup_dir)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise

    shutil.rmtree(stage_dir, ignore_errors=True)
    return [output_dir / filename for filename in filenames]


def main(repository_root: Path | None = None, argv: Sequence[str] | None = None) -> int:
    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    parser = argparse.ArgumentParser(description="Extract representative stills from Step 02 aligned videos.")
    parser.add_argument(
        "--settings",
        type=Path,
        default=root / "settings.json",
        help="Settings JSON path (relative paths resolve from the repository root; default: settings.json).",
    )
    parser.add_argument(
        "--camera-a",
        type=Path,
        help="Override the Camera A video input; relative paths resolve from the repository root.",
    )
    parser.add_argument(
        "--camera-b",
        type=Path,
        help="Override the Camera B video input; relative paths resolve from the repository root.",
    )
    args = parser.parse_args(argv)
    settings_path = args.settings.expanduser()
    if not settings_path.is_absolute():
        settings_path = root / settings_path
    settings_path = settings_path.resolve()
    try:
        with settings_path.open("r", encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
        if not isinstance(settings, dict):
            raise Step03Error("settings.json must contain a JSON object.")
        outputs = extract_aligned_images(settings, root, args.camera_a, args.camera_b)
    except (OSError, json.JSONDecodeError, Step03Error, ValueError) as exc:
        print(f"Step 03 failed: {exc}")
        return 1

    print(f"Created {len(outputs)} images in {outputs[0].parent}:")
    for output in outputs:
        print(output)
    return 0
