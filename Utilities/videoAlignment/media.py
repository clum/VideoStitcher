"""FFmpeg based media inspection and frame extraction."""

import json
import shutil
import struct
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

MAX_CLIP_SECONDS = Fraction(30)


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    # duration is the section available to the alignment UI; source_duration
    # records the full media duration reported by ffprobe.
    duration: Fraction
    source_duration: Fraction
    width: int | None
    height: int | None
    frame_rate: str | None
    has_audio: bool


def require_tools() -> tuple[str, str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    ffplay = shutil.which("ffplay")
    missing = [name for name, value in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe), ("ffplay", ffplay)) if not value]
    if missing:
        raise RuntimeError("FFmpeg tools not found: " + ", ".join(missing) + ". Install FFmpeg and ensure ffmpeg, ffprobe, and ffplay are on PATH.")
    return ffmpeg, ffprobe, ffplay


def probe(path: str | Path) -> MediaInfo:
    _, ffprobe, _ = require_tools()
    media_path = Path(path).expanduser().resolve()
    if not media_path.is_file():
        raise FileNotFoundError(f"File not found: {media_path}")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate,duration", "-of", "json", str(media_path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Could not read {media_path.name}")
    try:
        payload = json.loads(result.stdout)
        streams = payload.get("streams", [])
        video = next(s for s in streams if s.get("codec_type") == "video")
        duration_value = video.get("duration") or payload.get("format", {}).get("duration")
        duration = Fraction(str(duration_value))
        if duration <= 0:
            raise ValueError("duration must be positive")
    except (ValueError, TypeError, StopIteration, ZeroDivisionError) as exc:
        raise RuntimeError(f"Could not determine video duration and video stream for {media_path.name}.") from exc
    return MediaInfo(
        media_path, min(duration, MAX_CLIP_SECONDS), duration,
        int(video["width"]) if video.get("width") else None,
        int(video["height"]) if video.get("height") else None,
        video.get("avg_frame_rate"),
        any(s.get("codec_type") == "audio" for s in streams),
    )


def frame_png(info: MediaInfo, time: Fraction, size: tuple[int, int] = (520, 292)) -> bytes:
    ffmpeg, _, _ = require_tools()
    bounded = min(max(time, Fraction(0)), max(Fraction(0), info.duration - Fraction(1, 1000)))
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{float(bounded):.6f}", "-i", str(info.path), "-frames:v", "1", "-vf", f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=decrease,pad={size[0]}:{size[1]}:(ow-iw)/2:(oh-ih)/2:black", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        capture_output=True, check=False,
    )
    if result.returncode or not result.stdout:
        raise RuntimeError(f"Could not decode a frame from {info.path.name} at {float(bounded):.3f}s.")
    return result.stdout


def audio_peaks(info: MediaInfo, bins: int = 1200) -> list[float]:
    """Decode a compact amplitude envelope for the active audio window."""
    ffmpeg, _, _ = require_tools()
    if not info.has_audio:
        return []
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(info.path),
         "-t", f"{float(info.duration):.6f}", "-map", "0:a:0", "-vn", "-ac", "1",
         "-ar", "8000", "-f", "s16le", "pipe:1"],
        capture_output=True, check=False,
    )
    if result.returncode or not result.stdout:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or f"Could not read audio from {info.path.name}.")
    sample_count = len(result.stdout) // 2
    bin_count = max(1, min(bins, sample_count))
    peaks: list[float] = []
    max_peak = 0
    for bin_index in range(bin_count):
        start = bin_index * sample_count // bin_count
        stop = max(start + 1, (bin_index + 1) * sample_count // bin_count)
        peak = max(abs(struct.unpack_from("<h", result.stdout, sample * 2)[0]) for sample in range(start, min(stop, sample_count)))
        peaks.append(float(peak))
        max_peak = max(max_peak, peak)
    if max_peak:
        peaks = [peak / max_peak for peak in peaks]
    return peaks
