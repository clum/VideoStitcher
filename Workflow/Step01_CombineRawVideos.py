"""Combine each camera's raw video segments into one file.

Configure ``CameraA.RawVideoFilePath`` and ``CameraB.RawVideoFilePath`` as
folders in the repository-root settings.json, then run from the repo root:

    python Workflow/Step01_CombineRawVideos.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "settings.json"
OUTPUT_DIR = ROOT / "Results" / "Step01_CombinedVideos"
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mts", ".m2ts",
}
STREAM_SIGNATURE_FIELDS = (
    "codec_type", "codec_name", "codec_tag_string", "profile", "width", "height",
    "pix_fmt", "sample_fmt", "sample_rate", "channels", "channel_layout",
)


class WorkflowError(Exception):
    """An actionable workflow configuration or media error."""


class TerminalProgress:
    """Render combined FFmpeg progress for the complete two-camera workflow."""

    def __init__(self, total_seconds: float) -> None:
        self.total_seconds = max(total_seconds, 0.001)
        self.completed_seconds = 0.0
        self.last_line_length = 0

    def update(self, camera: str, camera_seconds: float, camera_total: float) -> None:
        overall = (self.completed_seconds + min(camera_seconds, camera_total)) / self.total_seconds
        self._render(camera, min(overall, 0.995))

    def complete_camera(self, camera: str, camera_total: float) -> None:
        self.completed_seconds += camera_total
        self._render(camera, min(self.completed_seconds / self.total_seconds, 0.995))

    def finish(self) -> None:
        self._render("Complete", 1.0)
        print()

    def _render(self, camera: str, fraction: float) -> None:
        fraction = max(0.0, min(fraction, 1.0))
        width = 36
        filled = int(width * fraction)
        line = f"\r[{('#' * filled):<{width}}] {fraction * 100:6.2f}%  {camera}"
        padding = " " * max(0, self.last_line_length - len(line))
        print(line + padding, end="", flush=True)
        self.last_line_length = len(line)


def _natural_name_key(path: Path) -> tuple[tuple[int, object], ...]:
    parts = re.split(r"(\d+)", path.name.casefold())
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)


def _load_settings() -> dict[str, Any]:
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowError(f"Settings file not found: {SETTINGS_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Settings file is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise WorkflowError(f"Could not read settings file {SETTINGS_PATH}: {exc}") from exc
    if not isinstance(settings, dict):
        raise WorkflowError("settings.json must contain a JSON object.")
    scenario = settings.get("ScenarioName")
    if not isinstance(scenario, str) or not scenario.strip():
        raise WorkflowError("Set a non-empty ScenarioName in settings.json.")
    return settings


def _scenario_prefix(scenario: str) -> str:
    prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", scenario).strip(" ._")
    if not prefix:
        raise WorkflowError("ScenarioName does not contain any valid filename characters.")
    return prefix


def _video_files(folder: Path, camera: str) -> list[Path]:
    if not folder.exists():
        raise WorkflowError(f"{camera}.RawVideoFilePath does not exist: {folder}")
    if not folder.is_dir():
        raise WorkflowError(f"{camera}.RawVideoFilePath must be a folder: {folder}")
    try:
        files = [
            path for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        ]
        files.sort(key=lambda path: (path.stat().st_mtime_ns, _natural_name_key(path), path.name.casefold()))
    except OSError as exc:
        raise WorkflowError(f"Could not list video files in {folder}: {exc}") from exc
    if not files:
        raise WorkflowError(f"No supported video files found directly inside {folder} for {camera}.")
    extensions = {path.suffix.casefold() for path in files}
    if len(extensions) != 1:
        found = ", ".join(sorted(extensions))
        raise WorkflowError(f"All {camera} clips must use the same file extension; found: {found}.")
    return files


def _stream_signature(ffprobe: str, path: Path) -> tuple[tuple[object, ...], ...]:
    fields = ",".join(STREAM_SIGNATURE_FIELDS)
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", f"stream={fields}", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise WorkflowError(f"Could not inspect {path.name}: {result.stderr.strip() or 'ffprobe failed.'}")
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"ffprobe returned invalid stream information for {path.name}.") from exc
    if not streams or not any(stream.get("codec_type") == "video" for stream in streams):
        raise WorkflowError(f"No video stream found in {path.name}.")
    # The camera MP4s include GoPro timecode/data tracks (for example fdsc)
    # that FFmpeg's MP4 muxer cannot copy. Compare and retain only A/V streams.
    av_streams = [stream for stream in streams if stream.get("codec_type") in {"video", "audio"}]
    return tuple(tuple(stream.get(field) for field in STREAM_SIGNATURE_FIELDS) for stream in av_streams)


def _duration_seconds(ffprobe: str, path: Path) -> float:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise WorkflowError(f"Could not read duration for {path.name}: {result.stderr.strip() or 'ffprobe failed.'}")
    try:
        duration = float(Fraction(result.stdout.strip()))
    except (ValueError, ZeroDivisionError) as exc:
        raise WorkflowError(f"Could not determine duration for {path.name}.") from exc
    if duration <= 0:
        raise WorkflowError(f"Invalid duration reported for {path.name}.")
    return duration


def _ffconcat_quote(path: Path) -> str:
    # Use POSIX separators in the manifest; FFmpeg's concat demuxer parses its
    # own quoting syntax, so a single quote is closed, escaped, and reopened.
    value = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{value}'"


def _combine_one(
    ffmpeg: str,
    ffprobe: str,
    camera: str,
    inputs: list[Path],
    temp_dir: Path,
    durations: list[float],
    progress: TerminalProgress,
) -> Path:
    camera_total = sum(durations)
    print(f"\nCombining {camera} ({len(inputs)} files):")
    for path in inputs:
        print(f"  {path}")

    signature = _stream_signature(ffprobe, inputs[0])
    for path in inputs[1:]:
        if _stream_signature(ffprobe, path) != signature:
            raise WorkflowError(
                f"Cannot combine {camera} clips with different stream layouts or codec settings. "
                f"The files must be compatible for stream-copy concatenation (first mismatch: {path.name})."
            )

    extension = inputs[0].suffix
    concat_list = temp_dir / f"{camera}_files.ffconcat"
    staged_output = temp_dir / f"{camera}{extension}"
    try:
        concat_list.write_text(
            "ffconcat version 1.0\n" + "\n".join(_ffconcat_quote(path) for path in inputs) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise WorkflowError(f"Could not create FFmpeg input list for {camera}: {exc}") from exc

    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-nostats",
        "-stats_period", "0.5", "-progress", "pipe:1",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-map", "0:v", "-map", "0:a?", "-c", "copy", str(staged_output),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    diagnostics = []
    current_seconds = 0.0
    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if "=" not in line:
            if line:
                diagnostics.append(line)
            continue
        key, value = line.split("=", 1)
        if key in {"out_time_us", "out_time_ms"}:
            try:
                current_seconds = int(value) / 1_000_000
                progress.update(camera, current_seconds, camera_total)
            except ValueError:
                pass
        elif key == "progress" and value == "end":
            current_seconds = camera_total
    return_code = process.wait()
    if return_code or not staged_output.is_file() or staged_output.stat().st_size == 0:
        progress._render(camera, min(progress.completed_seconds / progress.total_seconds, 0.995))
        print()
        raise WorkflowError(
            f"FFmpeg could not combine {camera} clips. The source files may not be compatible. "
            f"Details: {' '.join(diagnostics).strip() or 'No output was produced.'}"
        )
    validate = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(staged_output)],
        capture_output=True,
        text=True,
        check=False,
    )
    if validate.returncode:
        raise WorkflowError(f"The combined {camera} output could not be validated: {validate.stderr.strip()}")
    progress.complete_camera(camera, camera_total)
    return staged_output


def _publish_pair(staged: list[Path], destinations: list[Path], backup_dir: Path) -> None:
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for destination in destinations:
            if destination.exists():
                backup = backup_dir / f"{uuid.uuid4().hex}_{destination.name}"
                destination.replace(backup)
                backups[destination] = backup
        for temporary, destination in zip(staged, destinations):
            temporary.replace(destination)
            published.append(destination)
    except OSError as exc:
        for destination in published:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        rollback_errors = []
        for destination, backup in backups.items():
            try:
                if backup.exists():
                    backup.replace(destination)
            except OSError as rollback_error:
                rollback_errors.append(f"Could not restore {destination.name}: {rollback_error}")
        if rollback_errors:
            raise WorkflowError(f"Could not publish combined outputs: {exc}; {'; '.join(rollback_errors)}") from exc
        raise WorkflowError(f"Could not publish combined outputs: {exc}") from exc


def run() -> tuple[Path, Path]:
    settings = _load_settings()
    prefix = _scenario_prefix(settings["ScenarioName"].strip())
    camera_inputs: list[list[Path]] = []
    for camera in ("CameraA", "CameraB"):
        camera_settings = settings.get(camera)
        if not isinstance(camera_settings, dict):
            raise WorkflowError(f"Add a {camera} object to settings.json.")
        raw_folder = camera_settings.get("RawVideoFilePath")
        if not isinstance(raw_folder, str) or not raw_folder.strip():
            raise WorkflowError(f"Set {camera}.RawVideoFilePath to a folder in settings.json.")
        folder = Path(raw_folder).expanduser()
        if not folder.is_absolute():
            folder = ROOT / folder
        if folder.resolve() == OUTPUT_DIR.resolve():
            raise WorkflowError(f"{camera}.RawVideoFilePath cannot be the Step01_CombinedVideos output folder.")
        camera_inputs.append(_video_files(folder, camera))

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    missing_tools = [name for name, command in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not command]
    if missing_tools:
        raise WorkflowError("Required FFmpeg tools are not on PATH: " + ", ".join(missing_tools))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    durations_by_camera = [
        [_duration_seconds(ffprobe, path) for path in files]
        for files in camera_inputs
    ]
    progress = TerminalProgress(sum(sum(durations) for durations in durations_by_camera))
    destinations = [
        OUTPUT_DIR / f"{prefix}_CameraA{camera_inputs[0][0].suffix}",
        OUTPUT_DIR / f"{prefix}_CameraB{camera_inputs[1][0].suffix}",
    ]

    try:
        with tempfile.TemporaryDirectory(prefix=".step01_", dir=OUTPUT_DIR) as temporary:
            temp_dir = Path(temporary)
            staged = [
                _combine_one(ffmpeg, ffprobe, "CameraA", camera_inputs[0], temp_dir, durations_by_camera[0], progress),
                _combine_one(ffmpeg, ffprobe, "CameraB", camera_inputs[1], temp_dir, durations_by_camera[1], progress),
            ]
            _publish_pair(staged, destinations, temp_dir)
    except OSError as exc:
        raise WorkflowError(f"Could not prepare the combined video output folder: {exc}") from exc
    progress.finish()
    return destinations[0], destinations[1]


def main() -> int:
    try:
        path_a, path_b = run()
    except WorkflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Camera A combined video: {path_a}")
    print(f"Camera B combined video: {path_b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
