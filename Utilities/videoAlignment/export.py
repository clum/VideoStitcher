"""Create equal-duration aligned MP4 outputs with FFmpeg."""

import re
import subprocess
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Callable

from .alignment import calculate_alignment
from .media import MediaInfo, probe, require_tools


def _output_frame_rate(media: MediaInfo) -> Fraction:
    try:
        rate = Fraction(media.frame_rate or "30")
        return rate if rate > 0 else Fraction(30)
    except (ValueError, ZeroDivisionError):
        return Fraction(30)


def _scenario_prefix(scenario_name: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", scenario_name).strip(" ._")
    if not safe:
        raise ValueError("Set a valid ScenarioName in settings.json before exporting.")
    return safe


def _ffmpeg_command(
    ffmpeg: str,
    media: MediaInfo,
    trim_start: Fraction,
    frame_rate: Fraction,
    frame_count: int,
    target_duration: Fraction,
    output_path: Path,
) -> list[str]:
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
        "-ss", f"{float(trim_start):.9f}", "-i", str(media.path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"fps={frame_rate.numerator}/{frame_rate.denominator},trim=end_frame={frame_count},setpts=PTS-STARTPTS",
        "-frames:v", str(frame_count), "-t", f"{float(target_duration):.9f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
    ]
    if media.has_audio:
        duration = f"{float(target_duration):.9f}"
        cmd += [
            "-af", f"apad=pad_dur={duration},atrim=duration={duration},asetpts=PTS-STARTPTS",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        ]
    cmd += ["-movflags", "+faststart", str(output_path)]
    return cmd


def export_aligned_pair(
    media_a: MediaInfo,
    media_b: MediaInfo,
    offset: Fraction,
    scenario_name: str,
    output_dir: Path,
    progress: Callable[[str], None] | None = None,
) -> tuple[Path, Path, Fraction]:
    """Export A and B trimmed to their common interval, with matching frame counts."""
    ffmpeg, _, _ = require_tools()
    alignment = calculate_alignment(media_a.duration, media_b.duration, offset)
    if not alignment.overlap_exists:
        raise ValueError("The selected offset leaves no shared interval to export.")
    trims = alignment.trims(media_a.duration, media_b.duration)

    prefix = _scenario_prefix(scenario_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = 1
    while True:
        tag = "" if suffix == 1 else f"_{suffix:02d}"
        path_a = output_dir / f"{prefix}{tag}_CameraA_Aligned.mp4"
        path_b = output_dir / f"{prefix}{tag}_CameraB_Aligned.mp4"
        if not path_a.exists() and not path_b.exists():
            break
        suffix += 1

    frame_rate = _output_frame_rate(media_a)
    frame_count = max(1, int(alignment.duration * frame_rate + Fraction(1, 2)))
    target_duration = Fraction(frame_count, 1) / frame_rate
    token = uuid.uuid4().hex
    temp_a = output_dir / f".{token}_CameraA.mp4"
    temp_b = output_dir / f".{token}_CameraB.mp4"
    promoted: list[Path] = []
    try:
        for media, trim_start, temp_path, label in (
            (media_a, trims["a_start"], temp_a, "A"),
            (media_b, trims["b_start"], temp_b, "B"),
        ):
            if progress:
                progress(f"Encoding camera {label}…")
            result = subprocess.run(
                _ffmpeg_command(ffmpeg, media, trim_start, frame_rate, frame_count, target_duration, temp_path),
                capture_output=True, text=True, check=False,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or f"FFmpeg failed while exporting camera {label}.")

        # Both videos use the same output frame rate and frame count. Confirm the
        # encoded video stream durations before publishing either final filename.
        output_info_a = probe(temp_a)
        output_info_b = probe(temp_b)
        if output_info_a.source_duration != output_info_b.source_duration:
            raise RuntimeError(
                "FFmpeg produced different video durations for the pair "
                f"({float(output_info_a.source_duration):.6f}s and {float(output_info_b.source_duration):.6f}s)."
            )
        if path_a.exists() or path_b.exists():
            raise FileExistsError("An output file appeared during export. No existing file was overwritten.")
        temp_a.replace(path_a)
        promoted.append(path_a)
        temp_b.replace(path_b)
        promoted.append(path_b)
        if progress:
            progress("Aligned clips exported.")
        return path_a, path_b, output_info_a.source_duration
    except Exception:
        for path in (temp_a, temp_b, *promoted):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
