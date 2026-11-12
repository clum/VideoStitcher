"""Remove Step 08 intervals from a video while preserving its timeline settings."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

from .stitching_common import StitchingError, load_settings
from .workflow_paths import results_directory, scenario_prefix


class RemovalError(RuntimeError):
    """Actionable failure while removing marked sections from a video."""


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: Fraction
    duration: float
    has_audio: bool


TIME_PATTERN = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{3})$")
EXPECTED_HEADER = ["ID", "StartTime", "EndTime", "Comment"]
EDGE_THRESHOLD_SECONDS = 5.0


def _resolve_path(value: str | Path, root: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _tools() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RemovalError("FFmpeg and ffprobe must be installed and available on PATH for Step 08b.")
    return ffmpeg, ffprobe


def _probe(ffprobe: str, path: Path) -> tuple[dict[str, Any], MediaInfo]:
    if not path.is_file():
        raise RemovalError(f"Video file does not exist: {path}")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError as exc:
        raise RemovalError(f"Could not start ffprobe for {path}: {exc}") from exc
    if result.returncode:
        raise RemovalError(f"Could not inspect video {path}: {result.stderr.strip() or 'ffprobe failed'}")
    try:
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        width, height = int(video["width"]), int(video["height"])
        duration = float(video.get("duration") or payload["format"]["duration"])
        raw_rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
        fps = Fraction(raw_rate)
    except (KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise RemovalError(f"Could not determine valid video dimensions, frame rate, and duration for {path}.") from exc
    if width <= 0 or height <= 0 or fps <= 0 or not math.isfinite(duration) or duration <= 0:
        raise RemovalError(f"Video has invalid dimensions, frame rate, or duration: {path}")
    return payload, MediaInfo(
        width, height, fps, duration,
        any(stream.get("codec_type") == "audio" for stream in streams),
    )


def _parse_timestamp(value: str, row_number: int, column: str) -> float:
    match = TIME_PATTERN.fullmatch(value.strip())
    if not match:
        raise RemovalError(
            f"CSV row {row_number} has invalid {column} {value!r}; expected HH:MM:SS.mmm."
        )
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    if minutes >= 60 or seconds >= 60:
        raise RemovalError(f"CSV row {row_number} has an invalid {column} time {value!r}.")
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def _load_ranges(path: Path, duration: float) -> list[tuple[float, float]]:
    if not path.is_file():
        raise RemovalError(f"Removal CSV does not exist: {path}")
    ranges: list[tuple[float, float]] = []
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header != EXPECTED_HEADER:
                raise RemovalError(f"CSV header must be {','.join(EXPECTED_HEADER)} in that order: {path}")
            for row_number, row in enumerate(reader, start=2):
                if len(row) != 4:
                    raise RemovalError(f"CSV row {row_number} must contain exactly four columns.")
                start = _parse_timestamp(row[1], row_number, "StartTime")
                end = _parse_timestamp(row[2], row_number, "EndTime")
                if start < 0 or start >= end or end > duration:
                    raise RemovalError(
                        f"CSV row {row_number} must satisfy 0 <= StartTime < EndTime <= "
                        f"video duration ({duration:.3f}s)."
                    )
                ranges.append((start, end))
    except OSError as exc:
        raise RemovalError(f"Could not read removal CSV {path}: {exc}") from exc
    ranges.sort()
    for index, ((_, previous_end), (next_start, _)) in enumerate(zip(ranges, ranges[1:]), start=1):
        if next_start < previous_end:
            raise RemovalError(f"Removal CSV contains overlapping ranges near sorted interval {index}.")
    return ranges


def _effective_removals(
    ranges: list[tuple[float, float]], duration: float,
) -> list[tuple[float, float]]:
    if not ranges:
        return []
    effective = list(ranges)
    first_start, first_end = effective[0]
    if first_start <= EDGE_THRESHOLD_SECONDS:
        effective[0] = (0.0, first_end)
    last_start, last_end = effective[-1]
    if duration - last_end <= EDGE_THRESHOLD_SECONDS:
        effective[-1] = (last_start, duration)
    return effective


def _retained_segments(
    removals: list[tuple[float, float]], duration: float,
) -> list[tuple[float, float]]:
    retained: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in removals:
        if start > cursor:
            retained.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        retained.append((cursor, duration))
    return retained


def _filter_graph(segments: list[tuple[float, float]], has_audio: bool) -> str:
    chains: list[str] = []
    labels: list[str] = []
    audio_labels: list[str] = []
    for index, (start, end) in enumerate(segments):
        chains.append(
            f"[0:v:0]trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS[v{index}]"
        )
        labels.append(f"[v{index}]")
        if has_audio:
            chains.append(
                f"[0:a:0]atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS[a{index}]"
            )
            audio_labels.append(f"[a{index}]")
    if has_audio:
        chains.append(
            f"{''.join(label for pair in zip(labels, audio_labels) for label in pair)}"
            f"concat=n={len(segments)}:v=1:a=1[vout][aout]"
        )
    else:
        chains.append(f"{''.join(labels)}concat=n={len(segments)}:v=1:a=0[vout]")
    return ";".join(chains)


def _progress_time(value: str) -> float | None:
    try:
        return float(value) / 1_000_000.0
    except (TypeError, ValueError):
        return None


def _encode(
    ffmpeg: str,
    ffprobe: str,
    source: Path,
    temporary: Path,
    source_info: MediaInfo,
    segments: list[tuple[float, float]],
    expected_duration: float,
) -> None:
    graph = _filter_graph(segments, source_info.has_audio)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-progress", "pipe:1", "-nostats", "-i", str(source),
        "-filter_complex", graph, "-map", "[vout]",
    ]
    if source_info.has_audio:
        command.extend(["-map", "[aout]"])
    command.extend([
        "-map_metadata", "0", "-r", f"{source_info.fps.numerator}/{source_info.fps.denominator}",
        "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
    ])
    if source_info.has_audio:
        command.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000"])
    command.extend(["-movflags", "+faststart", str(temporary)])

    log_path = temporary.with_suffix(".ffmpeg.log")
    started = time.monotonic()
    last_percent = -1
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=log, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                key, separator, value = line.strip().partition("=")
                if not separator or key != "out_time_us":
                    continue
                output_time = _progress_time(value)
                if output_time is None:
                    continue
                percent = min(99, max(0, int(output_time * 100 / expected_duration)))
                if percent != last_percent:
                    elapsed = time.monotonic() - started
                    eta = elapsed * (expected_duration / output_time - 1) if output_time > 0 else None
                    eta_text = f"; ETA {eta:.0f}s" if eta is not None and math.isfinite(eta) else ""
                    print(
                        f"\rStep 08b progress: {percent:3d}% | output {output_time:.1f}/"
                        f"{expected_duration:.1f}s | elapsed {elapsed:.0f}s{eta_text}",
                        end="", flush=True,
                    )
                    last_percent = percent
            return_code = process.wait()
        if last_percent >= 0:
            print("\rStep 08b progress: 100%", flush=True)
        if return_code:
            detail = log_path.read_text(encoding="utf-8", errors="replace").strip()
            raise RemovalError(
                f"FFmpeg failed while removing sections (exit {return_code}): "
                f"{detail or f'diagnostics in {log_path}'}"
            )
    except OSError as exc:
        raise RemovalError(f"Could not run FFmpeg for {source}: {exc}") from exc

    if not temporary.is_file() or temporary.stat().st_size == 0:
        raise RemovalError(f"FFmpeg did not create an output video: {temporary}")
    _, output_info = _probe(ffprobe, temporary)
    if (output_info.width, output_info.height) != (source_info.width, source_info.height):
        raise RemovalError(
            f"Output dimensions are {output_info.width}x{output_info.height}; expected "
            f"{source_info.width}x{source_info.height}."
        )
    if abs(float(output_info.fps - source_info.fps)) > max(0.001, float(source_info.fps) * 1e-4):
        raise RemovalError(
            f"Output frame rate is {float(output_info.fps):.6g}; expected {float(source_info.fps):.6g} fps."
        )
    if output_info.has_audio != source_info.has_audio:
        raise RemovalError("Output audio-stream presence does not match the source video.")
    tolerance = max(1.0 / float(source_info.fps), 0.001) * (len(segments) + 1)
    if abs(output_info.duration - expected_duration) > tolerance:
        raise RemovalError(
            f"Output duration {output_info.duration:.3f}s differs from expected "
            f"{expected_duration:.3f}s by more than the allowed trim-boundary tolerance "
            f"({tolerance:.3f}s)."
        )
    log_path.unlink(missing_ok=True)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove Step 08 intervals from a stitched video.")
    parser.add_argument("--video", help="Source video; defaults to the configured Step 07 stitched MP4.")
    parser.add_argument("--removals", help="Step 08 CSV; defaults to the configured scenario CSV.")
    return parser.parse_args(argv)


def main(repository_root: Path | None = None, argv: Sequence[str] | None = None) -> int:
    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    args = _parse_args(argv)
    temporary: Path | None = None
    try:
        settings = load_settings(root)
        ffmpeg, ffprobe = _tools()
        scenario = scenario_prefix(settings["ScenarioName"])
        source = _resolve_path(args.video, root) if args.video else (
            results_directory(settings, root) / "Step07_StitchedVideos" / f"{scenario}_Stitched.mp4"
        ).resolve()
        removals_path = _resolve_path(args.removals, root) if args.removals else (
            results_directory(settings, root) / "Step08_IdentifyRemovals" / f"{scenario}_RemovalSegments.csv"
        ).resolve()
        _, source_info = _probe(ffprobe, source)
        raw_ranges = _load_ranges(removals_path, source_info.duration)
        removals = _effective_removals(raw_ranges, source_info.duration)
        segments = _retained_segments(removals, source_info.duration)
        if not segments:
            raise RemovalError("The removal ranges (including edge trimming) remove the entire video.")
        removed_duration = sum(end - start for start, end in removals)
        retained_duration = sum(end - start for start, end in segments)

        output_dir = results_directory(settings, root) / "Step08b_RemovedSections"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{scenario}_SectionsRemoved.mp4"
        if output.resolve() == source.resolve():
            raise RemovalError("The selected source video resolves to the Step 08b output path; refusing to overwrite it.")
        with tempfile.NamedTemporaryFile(prefix=".step08b_", suffix=".mp4", dir=output_dir, delete=False) as handle:
            temporary = Path(handle.name)

        print(f"Source video: {source}")
        print(f"Removal CSV: {removals_path}")
        print(f"Source duration: {source_info.duration:.3f}s; removed: {removed_duration:.3f}s; retained: {retained_duration:.3f}s")
        print("Audio: present" if source_info.has_audio else "Audio: absent; output will be silent.")
        if not raw_ranges:
            print("No sections were marked; creating a verified copy.")
        _encode(ffmpeg, ffprobe, source, temporary, source_info, segments, retained_duration)
        os.replace(temporary, output)
        temporary = None
        print(f"Sections-removed video saved to: {output.resolve()}")
        return 0
    except (StitchingError, RemovalError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Step 08b failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
