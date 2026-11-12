"""Review a stitched video and save intervals to be removed by a later step."""

from __future__ import annotations

import base64
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
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .stitching_common import StitchingError, load_settings
from .workflow_paths import results_directory, scenario_prefix


VIDEO_FILTERS = [
    ("Video files", "*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mts *.m2ts"),
    ("All files", "*.*"),
]
TIME_PATTERN = re.compile(r"^(\d+):(\d{2}):(\d{2})(?:\.(\d{1,3}))?$")


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool


@dataclass
class RemovalRange:
    uid: int
    start: float
    end: float
    comment: str


def _tools() -> tuple[str, str, str]:
    ffmpeg, ffprobe, ffplay = (shutil.which(name) for name in ("ffmpeg", "ffprobe", "ffplay"))
    missing = [name for name, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe), ("ffplay", ffplay)) if not path]
    if missing:
        raise StitchingError(
            "FFmpeg tools not found: " + ", ".join(missing)
            + ". Install FFmpeg with ffplay and ensure all three tools are available on PATH."
        )
    return ffmpeg, ffprobe, ffplay


def _fraction(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe_video(ffprobe: str, path: Path) -> VideoInfo:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise StitchingError(f"Video file does not exist: {path}")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate,duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError as exc:
        raise StitchingError(f"Could not start ffprobe for {path}: {exc}") from exc
    if result.returncode:
        raise StitchingError(f"Could not inspect video {path}: {result.stderr.strip() or 'ffprobe failed'}")
    try:
        payload = json.loads(result.stdout)
        stream = next(item for item in payload.get("streams", []) if item.get("codec_type") == "video")
        width, height = int(stream["width"]), int(stream["height"])
        fps = _fraction(stream.get("avg_frame_rate")) or _fraction(stream.get("r_frame_rate"))
        raw_duration = stream.get("duration") or payload.get("format", {}).get("duration")
        duration = float(raw_duration)
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StitchingError(f"Could not determine video dimensions, frame rate, and duration for {path}.") from exc
    if width <= 0 or height <= 0 or not math.isfinite(fps) or fps <= 0 or not math.isfinite(duration) or duration <= 0:
        raise StitchingError(f"Video has invalid dimensions, frame rate, or duration: {path}")
    has_audio = any(item.get("codec_type") == "audio" for item in payload.get("streams", []))
    return VideoInfo(path, width, height, fps, duration, has_audio)


def extract_preview(ffmpeg: str, video: VideoInfo, timestamp: float, size: tuple[int, int]) -> bytes:
    bounded = min(max(timestamp, 0.0), max(0.0, video.duration - 0.001))
    width, height = size
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=fast_bilinear,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    )
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{bounded:.6f}",
             "-i", str(video.path), "-frames:v", "1", "-vf", vf, "-f", "image2pipe",
             "-vcodec", "png", "pipe:1"],
            capture_output=True, check=False,
        )
    except OSError as exc:
        raise StitchingError(f"Could not start FFmpeg to preview {video.path}: {exc}") from exc
    if result.returncode or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise StitchingError(
            f"Could not decode a preview frame from {video.path.name} at {bounded:.3f}s"
            + (f": {detail}" if detail else ".")
        )
    return result.stdout


def format_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{millis:03d}"


def parse_timestamp(value: str) -> float:
    match = TIME_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError("Enter time as HH:MM:SS.mmm (milliseconds are optional).")
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if minutes >= 60 or seconds >= 60:
        raise ValueError("Minutes and seconds must each be between 00 and 59.")
    fractional = (match.group(4) or "").ljust(3, "0")
    millis = int(fractional) if fractional else 0
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def load_ranges(path: Path, duration: float, first_uid: int = 1) -> list[RemovalRange]:
    """Read and validate a complete Step 08 CSV before returning any ranges."""
    expected_header = ["ID", "StartTime", "EndTime", "Comment"]
    ranges: list[RemovalRange] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.reader(file)
            header = next(reader, None)
            if header != expected_header:
                raise ValueError("CSV header must be ID,StartTime,EndTime,Comment in that order.")
            for row_number, row in enumerate(reader, start=2):
                if len(row) != 4:
                    raise ValueError(f"CSV row {row_number} must contain exactly four columns.")
                try:
                    incoming_id = int(row[0])
                    if incoming_id <= 0:
                        raise ValueError("ID must be a positive integer.")
                    start = parse_timestamp(row[1])
                    end = parse_timestamp(row[2])
                except ValueError as exc:
                    raise ValueError(f"CSV row {row_number}: {exc}") from exc
                if not (0 <= start < end <= duration):
                    raise ValueError(
                        f"CSV row {row_number}: require 0 <= start < end <= video duration "
                        f"({format_timestamp(duration)})."
                    )
                ranges.append(RemovalRange(first_uid + len(ranges), start, end, row[3]))
    except (OSError, csv.Error) as exc:
        raise ValueError(f"Could not read CSV {path}: {exc}") from exc

    ranges.sort(key=lambda item: (item.start, item.end))
    for previous, current in zip(ranges, ranges[1:]):
        if current.start < previous.end:
            raise ValueError(
                f"Overlapping ranges: {format_timestamp(previous.start)}–{format_timestamp(previous.end)} "
                f"overlaps {format_timestamp(current.start)}–{format_timestamp(current.end)}."
            )
    return ranges


def save_ranges(path: Path, ranges: list[RemovalRange]) -> None:
    """Atomically replace a scenario CSV with validated, sorted range rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(ranges, key=lambda item: (item.start, item.end))
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", prefix=f".{path.stem}_",
            suffix=".tmp", dir=path.parent, delete=False,
        ) as file:
            temporary = Path(file.name)
            writer = csv.writer(file)
            writer.writerow(("ID", "StartTime", "EndTime", "Comment"))
            for index, item in enumerate(ordered, start=1):
                writer.writerow((index, format_timestamp(item.start), format_timestamp(item.end), item.comment))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


class RemovalEditor:
    PREVIEW_SIZE = (960, 540)

    def __init__(
        self, root: tk.Tk, settings: dict[str, Any], repository_root: Path,
        ffmpeg: str, ffprobe: str, ffplay: str,
    ):
        self.root = root
        self.settings = settings
        self.repository_root = repository_root
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.ffplay = ffplay
        self.default_output_path = results_directory(settings, repository_root) / "Step08_IdentifyRemovals" / (
            f"{scenario_prefix(settings['ScenarioName'])}_RemovalSegments.csv"
        )
        self.output_path = self.default_output_path
        self.video: VideoInfo | None = None
        self.ranges: list[RemovalRange] = []
        self.next_uid = 1
        self.selected_uid: int | None = None
        self.dirty = False
        self.playing = False
        self.play_started_at = 0.0
        self.play_started_position = 0.0
        self.audio_process: subprocess.Popen | None = None
        self.updating_timeline = False
        self.position = 0.0
        self.seek_id = 0
        self.preview_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="step08-preview")
        self.preview_future: Future[bytes] | None = None
        self.pending_preview: tuple[int, VideoInfo, float] | None = None
        self.preview_image: tk.PhotoImage | None = None
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self) -> None:
        self.root.title("Video Stitcher — Identify Removal Sections")
        self.root.minsize(900, 700)
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)

        top = ttk.Frame(self.root, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Button(top, text="Browse video…", command=self.browse_video).grid(row=0, column=0, sticky="w")
        self.path_var = tk.StringVar(value="No video loaded")
        ttk.Label(top, textvariable=self.path_var, anchor="w", wraplength=1050).grid(
            row=0, column=1, sticky="ew", padx=(10, 0)
        )
        self.info_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.info_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))

        body = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        preview_frame = ttk.LabelFrame(body, text="Video preview", padding=8)
        preview_frame.grid(row=0, column=0, sticky="nsew")
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        self.preview = tk.Label(
            preview_frame, text="Load a video to begin", background="#171717", foreground="white",
            anchor="center", width=120, height=34,
        )
        self.preview.grid(row=0, column=0, sticky="nsew")
        self.clock_var = tk.StringVar(value="00:00:00.000 / 00:00:00.000")
        ttk.Label(preview_frame, textvariable=self.clock_var, font=("Segoe UI", 11, "bold")).grid(
            row=1, column=0, sticky="e", pady=(6, 0)
        )

        playback = ttk.Frame(body, padding=(0, 8, 0, 0))
        playback.grid(row=1, column=0, sticky="ew")
        playback.columnconfigure(2, weight=1)
        self.play_button = ttk.Button(playback, text="Play", command=self.toggle_play, state="disabled")
        self.play_button.grid(row=0, column=0, sticky="w")
        ttk.Button(playback, text="Stop", command=self.stop_play).grid(row=0, column=1, padx=(6, 10), sticky="w")
        self.position_var = tk.DoubleVar(value=0.0)
        self.scrubber = ttk.Scale(playback, from_=0, to=1, variable=self.position_var, command=self.scrubbed, state="disabled")
        self.scrubber.grid(row=0, column=2, sticky="ew")

        edit = ttk.LabelFrame(body, text="Removal intervals", padding=8)
        edit.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        for col in (1, 3, 5):
            edit.columnconfigure(col, weight=1)
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        ttk.Label(edit, text="Start (HH:MM:SS.mmm)").grid(row=0, column=0, sticky="w")
        ttk.Entry(edit, textvariable=self.start_var, width=17).grid(row=0, column=1, sticky="ew", padx=(4, 4))
        ttk.Button(edit, text="Use playhead", command=lambda: self.set_field_to_position(self.start_var)).grid(row=0, column=2, sticky="w")
        ttk.Label(edit, text="End").grid(row=0, column=3, sticky="e", padx=(8, 0))
        ttk.Entry(edit, textvariable=self.end_var, width=17).grid(row=0, column=4, sticky="ew", padx=(4, 4))
        ttk.Button(edit, text="Use playhead", command=lambda: self.set_field_to_position(self.end_var)).grid(row=0, column=5, sticky="w")
        ttk.Label(edit, text="Comment (optional)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.comment_text = tk.Text(edit, height=2, wrap="word")
        self.comment_text.grid(row=1, column=1, columnspan=5, sticky="ew", padx=(4, 0), pady=(6, 0))
        actions = ttk.Frame(edit)
        actions.grid(row=0, column=6, rowspan=2, padx=(10, 0), sticky="e")
        self.add_button = ttk.Button(actions, text="Add range", command=self.add_range, state="disabled")
        self.add_button.pack(side="left")
        self.update_button = ttk.Button(actions, text="Update selected", command=self.update_range, state="disabled")
        self.update_button.pack(side="left", padx=(5, 0))
        self.remove_button = ttk.Button(actions, text="Remove selected", command=self.remove_range, state="disabled")
        self.remove_button.pack(side="left", padx=(5, 0))
        ttk.Button(actions, text="Clear fields", command=self.clear_fields).pack(side="left", padx=(5, 0))

        columns = ("id", "start", "end", "comment")
        self.tree = ttk.Treeview(edit, columns=columns, show="headings", height=6, selectmode="browse")
        self.tree.heading("id", text="ID")
        self.tree.heading("start", text="Start")
        self.tree.heading("end", text="End")
        self.tree.heading("comment", text="Comment")
        self.tree.column("id", width=55, stretch=False, anchor="center")
        self.tree.column("start", width=145, stretch=False)
        self.tree.column("end", width=145, stretch=False)
        self.tree.column("comment", width=400, stretch=True)
        self.tree.grid(row=2, column=0, columnspan=7, sticky="ew", pady=(8, 0))
        self.tree.bind("<<TreeviewSelect>>", self.on_select_range)

        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Load a video to identify intervals.")
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.output_path_var = tk.StringVar(value=f"CSV: {self.output_path}")
        ttk.Label(bottom, textvariable=self.output_path_var, wraplength=900).grid(
            row=1, column=0, sticky="w", pady=(3, 0)
        )
        self.browse_output_button = ttk.Button(
            bottom, text="Browse CSV…", command=self.browse_output_csv, state="disabled"
        )
        self.browse_output_button.grid(row=1, column=1, sticky="e", padx=(8, 0))
        self.save_button = ttk.Button(bottom, text="Save CSV", command=self.save_csv, state="disabled")
        self.save_button.grid(row=0, column=2, rowspan=2, sticky="e", padx=(8, 0))

    def browse_video(self) -> None:
        selected = filedialog.askopenfilename(title="Select stitched video", filetypes=VIDEO_FILTERS, parent=self.root)
        if not selected:
            return
        try:
            candidate = probe_video(self.ffprobe, Path(selected))
        except StitchingError as exc:
            messagebox.showerror("Could not open video", str(exc), parent=self.root)
            return
        if not self._confirm_discard_or_save():
            return
        self.load_video(candidate)

    def load_video(self, video: VideoInfo) -> None:
        self.pause_playback()
        self.video = video
        self.output_path = self.default_output_path
        self.output_path_var.set(f"CSV: {self.output_path}")
        self.ranges.clear()
        self.next_uid = 1
        self.selected_uid = None
        self.position = 0.0
        self.position_var.set(0.0)
        self.start_var.set("")
        self.end_var.set("")
        self.comment_text.delete("1.0", "end")
        self._refresh_ranges()
        self.dirty = False
        self.path_var.set(str(video.path))
        audio = "audio available" if video.has_audio else "no audio stream"
        self.info_var.set(
            f"Source: {video.width}×{video.height} · {video.fps:.6g} fps · "
            f"{format_timestamp(video.duration)} · {audio}"
        )
        self.scrubber.configure(from_=0, to=video.duration, state="normal")
        self.play_button.configure(state="normal")
        self.add_button.configure(state="normal")
        self.save_button.configure(state="normal")
        self.browse_output_button.configure(state="normal")
        self.clock_var.set(f"{format_timestamp(0)} / {format_timestamp(video.duration)}")
        self.status_var.set("Ready. Mark a start and end time, then add a range.")
        self.request_preview(0.0)

    def _confirm_discard_or_save(self, action: str = "loading another video") -> bool:
        if not self.dirty and not self.ranges:
            return True
        choice = messagebox.askyesnocancel(
            "Current ranges will be replaced",
            f"Save the current ranges before {action}?",
            parent=self.root,
        )
        if choice is None:
            return False
        if choice:
            return self.save_csv()
        return True

    def browse_output_csv(self) -> None:
        if self.video is None:
            messagebox.showerror("No video loaded", "Load a video before selecting a removal CSV.", parent=self.root)
            return
        selected = filedialog.asksaveasfilename(
            title="Select removal CSV",
            parent=self.root,
            initialdir=str(
                self.output_path.parent if self.output_path.parent.is_dir() else self.repository_root
            ),
            initialfile=self.output_path.name,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            confirmoverwrite=False,
        )
        if not selected:
            return
        candidate = Path(selected).expanduser().resolve()
        if candidate.suffix.lower() != ".csv":
            messagebox.showerror("Invalid output file", "Choose a file with the .csv extension.", parent=self.root)
            return

        if candidate.exists():
            if not candidate.is_file():
                messagebox.showerror("Invalid output file", f"Not a file: {candidate}", parent=self.root)
                return
            try:
                imported = load_ranges(candidate, self.video.duration)
            except (OSError, ValueError) as exc:
                messagebox.showerror("Could not load CSV", str(exc), parent=self.root)
                return
            if not self._confirm_discard_or_save("loading ranges from another CSV"):
                return
            self.output_path = candidate
            self.output_path_var.set(f"CSV: {self.output_path}")
            self.ranges = imported
            self.next_uid = len(imported) + 1
            self.selected_uid = None
            self._refresh_ranges()
            self.clear_fields()
            self.dirty = False
            self.status_var.set(f"Loaded {len(imported)} interval(s) from {candidate}")
            return

        self.output_path = candidate
        self.output_path_var.set(f"CSV: {self.output_path}")
        self.status_var.set(f"New CSV destination selected: {candidate}")

    def set_field_to_position(self, variable: tk.StringVar) -> None:
        if self.video is not None:
            variable.set(format_timestamp(self.position))

    def scrubbed(self, raw_value: str) -> None:
        if self.video is None or self.updating_timeline:
            return
        self.position = min(max(float(raw_value), 0.0), self.video.duration)
        self.clock_var.set(f"{format_timestamp(self.position)} / {format_timestamp(self.video.duration)}")
        if self.playing:
            self._start_audio(self.position)
            self.play_started_at = time.monotonic()
            self.play_started_position = self.position
        self.request_preview(self.position)

    def request_preview(self, timestamp: float) -> None:
        if self.video is None:
            return
        self.seek_id += 1
        request_id = self.seek_id
        video = self.video
        if self.preview_future is not None and not self.preview_future.done():
            self.pending_preview = (request_id, video, timestamp)
            return
        self.pending_preview = None
        self._submit_preview(request_id, video, timestamp)

    def _submit_preview(self, request_id: int, video: VideoInfo, timestamp: float) -> None:
        future = self.preview_executor.submit(extract_preview, self.ffmpeg, video, timestamp, self.PREVIEW_SIZE)
        self.preview_future = future

        def completed(done: Future[bytes]) -> None:
            try:
                png = done.result()
                error = None
            except Exception as exc:  # Marshal worker errors into Tk's main thread.
                png, error = None, str(exc)
            try:
                self.root.after(0, self._preview_finished, done, request_id, video.path, png, error)
            except tk.TclError:
                pass

        future.add_done_callback(completed)

    def _preview_finished(
        self, finished: Future[bytes], request_id: int, path: Path, png: bytes | None, error: str | None
    ) -> None:
        self.show_preview(request_id, path, png, error)
        if self.preview_future is finished:
            self.preview_future = None
        pending = self.pending_preview
        if pending is not None and (self.preview_future is None or self.preview_future.done()):
            self.pending_preview = None
            pending_id, video, timestamp = pending
            self._submit_preview(pending_id, video, timestamp)

    def show_preview(self, request_id: int, path: Path, png: bytes | None, error: str | None) -> None:
        if request_id != self.seek_id or self.video is None or self.video.path != path:
            return
        if error or png is None:
            self.status_var.set(error or "Could not decode preview frame.")
            return
        try:
            self.preview_image = tk.PhotoImage(data=base64.b64encode(png))
            self.preview.configure(image=self.preview_image, text="")
        except tk.TclError as exc:
            self.status_var.set(f"Could not display preview image: {exc}")
            return
        self.status_var.set("Preview ready. Mark a start and end time, then add a range.")

    def toggle_play(self) -> None:
        if self.video is None:
            return
        if self.playing:
            self.pause_playback()
            return
        if self.position >= self.video.duration:
            self.set_position(0.0)
        if self.video.has_audio:
            self._start_audio(self.position, notify=True)
        else:
            self.status_var.set("This video has no audio stream; playing video only.")
        self.playing = True
        self.play_started_at = time.monotonic()
        self.play_started_position = self.position
        self.play_button.configure(text="Pause")
        self._play_tick()

    def _play_tick(self) -> None:
        if not self.playing or self.video is None:
            return
        new_position = self.play_started_position + (time.monotonic() - self.play_started_at)
        if new_position >= self.video.duration:
            self.set_position(self.video.duration)
            self.pause_playback()
            return
        if self.audio_process is not None and self.audio_process.poll() is not None:
            return_code = self.audio_process.returncode
            self.audio_process = None
            if return_code:
                self.status_var.set(f"Audio playback ended with an error (code {return_code}); video continues.")
            else:
                self.status_var.set("Audio track ended; video continues.")
        self.set_position(new_position, request_frame=False)
        # Display fewer preview frames than the source while keeping the clock on the source timeline.
        if not hasattr(self, "last_play_preview") or time.monotonic() - self.last_play_preview >= 0.5:
            self.last_play_preview = time.monotonic()
            self.request_preview(new_position)
        self.root.after(50, self._play_tick)

    def set_position(self, value: float, request_frame: bool = True) -> None:
        if self.video is None:
            return
        self.position = min(max(float(value), 0.0), self.video.duration)
        self.updating_timeline = True
        try:
            self.position_var.set(self.position)
        finally:
            self.updating_timeline = False
        self.clock_var.set(f"{format_timestamp(self.position)} / {format_timestamp(self.video.duration)}")
        if request_frame:
            self.request_preview(self.position)

    def pause_playback(self) -> None:
        self.playing = False
        self._stop_audio()
        if hasattr(self, "play_button"):
            self.play_button.configure(text="Play")

    def _start_audio(self, timestamp: float, notify: bool = False) -> bool:
        self._stop_audio()
        if self.video is None or not self.video.has_audio:
            return True
        try:
            self.audio_process = subprocess.Popen(
                [self.ffplay, "-hide_banner", "-loglevel", "error", "-nodisp", "-autoexit",
                 "-ss", f"{max(0.0, timestamp):.6f}", "-i", str(self.video.path), "-vn"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except OSError as exc:
            self.audio_process = None
            self.status_var.set(f"Could not start audio playback: {exc}")
            if notify:
                messagebox.showerror("Audio playback unavailable", str(exc), parent=self.root)
            return False

    def _stop_audio(self) -> None:
        process, self.audio_process = self.audio_process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def stop_play(self) -> None:
        self.pause_playback()
        self.set_position(0.0)

    def add_range(self) -> None:
        self._create_or_update(None)

    def update_range(self) -> None:
        self._create_or_update(self.selected_uid)

    def _create_or_update(self, uid: int | None) -> None:
        if self.video is None:
            return
        try:
            start = parse_timestamp(self.start_var.get())
            end = parse_timestamp(self.end_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid time", str(exc), parent=self.root)
            return
        if not (0 <= start < end <= self.video.duration):
            messagebox.showerror(
                "Invalid interval", f"Require 0 ≤ start < end ≤ video duration ({format_timestamp(self.video.duration)}).",
                parent=self.root,
            )
            return
        for existing in self.ranges:
            if existing.uid != uid and start < existing.end and existing.start < end:
                messagebox.showerror(
                    "Overlapping interval",
                    f"This interval overlaps {format_timestamp(existing.start)}–{format_timestamp(existing.end)}. "
                    "Removal intervals must not overlap.", parent=self.root,
                )
                return
        comment = self.comment_text.get("1.0", "end-1c")
        if uid is None:
            created_uid = self.next_uid
            self.ranges.append(RemovalRange(created_uid, start, end, comment))
            self.next_uid += 1
        else:
            created_uid = uid
            existing = next((item for item in self.ranges if item.uid == uid), None)
            if existing is None:
                messagebox.showerror("Selection changed", "Select a range and try again.", parent=self.root)
                return
            existing.start, existing.end, existing.comment = start, end, comment
        self.ranges.sort(key=lambda item: (item.start, item.end))
        self.dirty = True
        self._refresh_ranges(select_uid=created_uid)
        self.status_var.set("Range updated. Save CSV to keep these changes.")

    def _refresh_ranges(self, select_uid: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        ordered = sorted(self.ranges, key=lambda item: (item.start, item.end))
        for index, item in enumerate(ordered, start=1):
            self.tree.insert(
                "", "end", iid=str(item.uid),
                values=(index, format_timestamp(item.start), format_timestamp(item.end), item.comment.replace("\n", " ")),
            )
        if select_uid is not None and str(select_uid) in self.tree.get_children():
            self.tree.selection_set(str(select_uid))
            self.tree.focus(str(select_uid))
            self.selected_uid = select_uid
        else:
            self.selected_uid = None
        self.update_button.configure(state="normal" if self.selected_uid is not None else "disabled")
        self.remove_button.configure(state="normal" if self.selected_uid is not None else "disabled")

    def on_select_range(self, _event: tk.Event | None = None) -> None:
        selected = self.tree.selection()
        if not selected:
            self.selected_uid = None
            self.update_button.configure(state="disabled")
            self.remove_button.configure(state="disabled")
            return
        self.selected_uid = int(selected[0])
        item = next((row for row in self.ranges if row.uid == self.selected_uid), None)
        if item is None:
            return
        self.start_var.set(format_timestamp(item.start))
        self.end_var.set(format_timestamp(item.end))
        self.comment_text.delete("1.0", "end")
        self.comment_text.insert("1.0", item.comment)
        self.update_button.configure(state="normal")
        self.remove_button.configure(state="normal")

    def remove_range(self) -> None:
        if self.selected_uid is None:
            return
        self.ranges = [item for item in self.ranges if item.uid != self.selected_uid]
        self.selected_uid = None
        self.dirty = True
        self._refresh_ranges()
        self.clear_fields()
        self.status_var.set("Range removed. Save CSV to keep these changes.")

    def clear_fields(self) -> None:
        self.start_var.set("")
        self.end_var.set("")
        self.comment_text.delete("1.0", "end")
        self.selected_uid = None
        self.tree.selection_remove(*self.tree.selection())
        self.update_button.configure(state="disabled")
        self.remove_button.configure(state="disabled")

    def save_csv(self) -> bool:
        if self.video is None:
            messagebox.showerror("No video loaded", "Load a video before saving intervals.", parent=self.root)
            return False
        try:
            save_ranges(self.output_path, self.ranges)
        except OSError as exc:
            messagebox.showerror(
                "Could not save CSV", f"Destination: {self.output_path}\n\n{exc}", parent=self.root
            )
            return False
        self.dirty = False
        self.status_var.set(f"Saved {len(self.ranges)} interval(s) to {self.output_path}")
        return True

    def close(self) -> None:
        if self.dirty:
            choice = messagebox.askyesnocancel(
                "Unsaved removal ranges", "Save removal ranges before closing?", parent=self.root
            )
            if choice is None:
                return
            if choice and not self.save_csv():
                return
        self.pause_playback()
        self.seek_id += 1
        self.preview_executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main(repository_root: Path | None = None, argv: list[str] | None = None) -> int:
    root_path = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    parser = argparse.ArgumentParser(description="Review a stitched video and mark intervals for later removal.")
    parser.add_argument("--video", help="Video to load (relative paths resolve from the repository root).")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(root_path)
        ffmpeg, ffprobe, ffplay = _tools()
        default_video = Path(args.video).expanduser() if args.video else (
            results_directory(settings, root_path) / "Step07_StitchedVideos"
            / f"{scenario_prefix(settings['ScenarioName'])}_Stitched.mp4"
        )
        if not default_video.is_absolute():
            default_video = root_path / default_video
        default_video = default_video.resolve()
    except (StitchingError, OSError, ValueError) as exc:
        print(f"Step 08 could not start: {exc}", file=sys.stderr)
        return 1
    startup_error: str | None = None
    try:
        default_info = probe_video(ffprobe, default_video)
    except StitchingError as exc:
        # Keep Browse available if the configured or explicitly requested path is invalid.
        default_info = None
        label = "specified" if args.video else "default"
        startup_error = f"Could not load {label} video {default_video}: {exc} Use Browse video… to select a file."
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        print(f"Step 08 could not open its Tkinter window: {exc}", file=sys.stderr)
        return 1
    app = RemovalEditor(window, settings, root_path, ffmpeg, ffprobe, ffplay)
    if default_info is not None:
        app.load_video(default_info)
    elif startup_error:
        app.status_var.set(startup_error)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
