"""Reusable FFmpeg video resizing and frame-rate conversion helpers."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any


class VideoConversionError(RuntimeError):
    """Raised when an input video cannot be converted or verified."""


def _required_tool(name: str) -> str:
    tool = shutil.which(name)
    if not tool:
        raise VideoConversionError(f"{name} was not found on PATH. Install FFmpeg and ensure {name} is available.")
    return tool


def _run_probe(ffprobe: str, path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise VideoConversionError(f"Could not run ffprobe for {path}: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or "ffprobe failed without a diagnostic message"
        raise VideoConversionError(f"Could not inspect {path}: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VideoConversionError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise VideoConversionError(f"ffprobe returned an invalid result for {path}.")
    return payload


def _positive_duration(payload: dict[str, Any], path: Path) -> float:
    try:
        duration = float(payload.get("format", {}).get("duration"))
    except (TypeError, ValueError) as exc:
        raise VideoConversionError(f"Could not determine the duration of {path}.") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise VideoConversionError(f"Video duration must be positive and finite: {path}")
    return duration


def _video_stream(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    stream = next(
        (item for item in payload.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    if stream is None:
        raise VideoConversionError(f"No video stream was found in {path}.")
    return stream


def _frame_rate(stream: dict[str, Any], path: Path) -> float:
    for field in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(field)
        if not isinstance(raw, str) or "/" not in raw:
            continue
        try:
            value = float(Fraction(raw))
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(value) and value > 0:
            return value
    raise VideoConversionError(f"Could not determine a valid frame rate for {path}.")


def _target_rate(value: int | float) -> Fraction:
    if isinstance(value, bool):
        raise VideoConversionError("fps must be a positive finite number.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise VideoConversionError("fps must be a positive finite number.") from exc
    if not math.isfinite(numeric) or numeric <= 0:
        raise VideoConversionError("fps must be a positive finite number.")
    try:
        rate = Fraction(str(value)).limit_denominator(1_000_000)
    except (ValueError, ZeroDivisionError) as exc:
        raise VideoConversionError("fps must be a positive finite number.") from exc
    if rate <= 0:
        raise VideoConversionError("fps must be a positive finite number.")
    return rate


def convert_video(
    input_path: str | Path,
    output_path: str | Path,
    width: int = 640,
    height: int = 360,
    fps: int | float = 10,
) -> Path:
    """Create a smaller H.264 copy, retaining audio streams and source duration.

    The output container is selected from ``output_path``'s extension. The
    source aspect ratio is preserved and letterboxed to the exact requested
    dimensions. Audio is copied without re-encoding when present. A verified
    temporary file replaces an existing output only after conversion succeeds.

    Raises:
        VideoConversionError: If the paths, conversion settings, codecs, or
            generated output are invalid or FFmpeg fails.
    """
    try:
        source = Path(input_path).expanduser().resolve()
        destination = Path(output_path).expanduser().resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise VideoConversionError(f"Invalid input or output path: {exc}") from exc

    if not source.is_file():
        raise VideoConversionError(f"Input video does not exist or is not a file: {source}")
    if source == destination:
        raise VideoConversionError("Input and output paths must be different; the source video is protected.")
    if not destination.suffix:
        raise VideoConversionError(f"Output path must have a container extension: {destination}")
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in (width, height)):
        raise VideoConversionError("width and height must be positive integers.")
    if width % 2 or height % 2:
        raise VideoConversionError("width and height must be even for H.264 yuv420p output.")
    rate = _target_rate(fps)
    ffmpeg, ffprobe = _required_tool("ffmpeg"), _required_tool("ffprobe")

    source_probe = _run_probe(ffprobe, source)
    source_video = _video_stream(source_probe, source)
    source_duration = _positive_duration(source_probe, source)
    source_audio = [
        stream for stream in source_probe.get("streams", []) if stream.get("codec_type") == "audio"
    ]
    try:
        source_width, source_height = int(source_video["width"]), int(source_video["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoConversionError(f"Could not determine video dimensions for {source}.") from exc
    if source_width <= 0 or source_height <= 0:
        raise VideoConversionError(f"Input video has invalid dimensions: {source}")
    _frame_rate(source_video, source)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise VideoConversionError(f"Could not create output directory {destination.parent}: {exc}") from exc

    rate_arg = f"{rate.numerator}/{rate.denominator}"
    video_filter = (
        f"fps={rate_arg},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.stem}_",
            suffix=destination.suffix,
            dir=destination.parent,
            delete=False,
        ) as temp_file:
            temporary_path = Path(temp_file.name)
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-map_metadata",
            "0",
            "-vf",
            video_filter,
            "-fps_mode",
            "cfr",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
        ]
        if source_audio:
            command.extend(["-c:a", "copy"])
        command.append(str(temporary_path))
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        except OSError as exc:
            raise VideoConversionError(f"Could not start FFmpeg for {source}: {exc}") from exc
        if result.returncode:
            detail = result.stderr.strip() or f"FFmpeg exited with status {result.returncode}"
            raise VideoConversionError(
                f"Could not convert {source} to {destination}: {detail}"
            )
        if not temporary_path.is_file() or temporary_path.stat().st_size <= 0:
            raise VideoConversionError(f"FFmpeg did not create a valid output file for {destination}.")

        output_probe = _run_probe(ffprobe, temporary_path)
        output_video = _video_stream(output_probe, temporary_path)
        output_duration = _positive_duration(output_probe, temporary_path)
        try:
            actual_width, actual_height = int(output_video["width"]), int(output_video["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VideoConversionError(f"Could not verify output dimensions for {destination}.") from exc
        output_rate = _frame_rate(output_video, temporary_path)
        output_has_audio = any(
            stream.get("codec_type") == "audio" for stream in output_probe.get("streams", [])
        )
        tolerance = 1.0 / float(rate) + 0.002
        if (actual_width, actual_height) != (width, height):
            raise VideoConversionError(
                f"Output dimensions are {actual_width}x{actual_height}; expected {width}x{height}."
            )
        if abs(output_rate - float(rate)) > max(0.001, float(rate) * 1e-4):
            raise VideoConversionError(
                f"Output frame rate is {output_rate:g} fps; expected {float(rate):g} fps."
            )
        if output_has_audio != bool(source_audio):
            raise VideoConversionError(
                "Output audio-stream presence does not match the source video."
            )
        if abs(output_duration - source_duration) > tolerance:
            raise VideoConversionError(
                f"Output duration {output_duration:.6f}s differs from source duration "
                f"{source_duration:.6f}s by more than one output frame ({1.0 / float(rate):.6f}s)."
            )
        try:
            os.replace(temporary_path, destination)
            temporary_path = None
        except OSError as exc:
            raise VideoConversionError(f"Could not replace output file {destination}: {exc}") from exc
        return destination
    except VideoConversionError:
        raise
    except OSError as exc:
        raise VideoConversionError(f"Video conversion failed for {source} -> {destination}: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
