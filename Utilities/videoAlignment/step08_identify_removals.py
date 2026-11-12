"""Review a stitched video and save intervals to be removed by a later step."""

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .stitching_common import StitchingError, load_settings
from .workflow_paths import results_directory, scenario_prefix


QT_VIDEO_FILTER = "Video files (*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mts *.m2ts);;All files (*)"
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


try:
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QSlider,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    QT_IMPORT_ERROR: Exception | None = None
except (ImportError, OSError) as exc:  # Give users a useful install command if PySide6 is missing.
    QT_IMPORT_ERROR = exc
    QMainWindow = object  # type: ignore[assignment,misc]


def _ffprobe_tool() -> str:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise StitchingError("ffprobe was not found on PATH. Install FFmpeg and ensure ffprobe is available.")
    return ffprobe


def _fraction(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe_video(ffprobe: str, path: Path) -> VideoInfo:
    """Validate the video stream and obtain basic metadata before Qt loads it."""
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
    """Atomically replace a CSV with sorted range rows and sequential IDs."""
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


if QT_IMPORT_ERROR is None:
    class RemovalEditor(QMainWindow):
        """Step 08 editor whose media player owns the playback clock."""

        def __init__(
            self, settings: dict[str, Any], repository_root: Path, ffprobe: str,
        ):
            super().__init__()
            self.settings = settings
            self.repository_root = repository_root
            self.ffprobe = ffprobe
            self.default_output_path = results_directory(settings, repository_root) / "Step08_IdentifyRemovals" / (
                f"{scenario_prefix(settings['ScenarioName'])}_RemovalSegments.csv"
            )
            self.output_path = self.default_output_path
            self.video: VideoInfo | None = None
            self.duration = 0.0
            self.media_ready = False
            self.ranges: list[RemovalRange] = []
            self.next_uid = 1
            self.selected_uid: int | None = None
            self.dirty = False
            self.updating_slider = False

            self.setWindowTitle("Video Stitcher — Identify Removal Sections")
            self.resize(1200, 900)
            self._build_ui()

            self.audio_output = QAudioOutput(self)
            self.player = QMediaPlayer(self)
            self.player.setAudioOutput(self.audio_output)
            self.player.setVideoOutput(self.video_widget)
            self.player.positionChanged.connect(self._position_changed)
            self.player.durationChanged.connect(self._duration_changed)
            self.player.mediaStatusChanged.connect(self._media_status_changed)
            self.player.playbackStateChanged.connect(self._playback_state_changed)
            self.player.errorOccurred.connect(self._media_error)

        def _build_ui(self) -> None:
            central = QWidget(self)
            self.setCentralWidget(central)
            layout = QVBoxLayout(central)

            video_row = QHBoxLayout()
            self.browse_video_button = QPushButton("Browse video…")
            self.browse_video_button.clicked.connect(self.browse_video)
            video_row.addWidget(self.browse_video_button)
            self.path_label = QLabel("No video loaded")
            self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.path_label.setWordWrap(True)
            video_row.addWidget(self.path_label, stretch=1)
            layout.addLayout(video_row)
            self.info_label = QLabel("")
            layout.addWidget(self.info_label)

            self.video_widget = QVideoWidget()
            self.video_widget.setMinimumSize(640, 360)
            self.video_widget.setStyleSheet("background-color: #171717;")
            layout.addWidget(self.video_widget, stretch=1)
            self.clock_label = QLabel("00:00:00.000 / 00:00:00.000")
            self.clock_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            layout.addWidget(self.clock_label)

            playback = QHBoxLayout()
            self.play_button = QPushButton("Play")
            self.play_button.clicked.connect(self.toggle_play)
            self.play_button.setEnabled(False)
            playback.addWidget(self.play_button)
            self.stop_button = QPushButton("Stop")
            self.stop_button.clicked.connect(self.stop_play)
            self.stop_button.setEnabled(False)
            playback.addWidget(self.stop_button)
            self.scrubber = QSlider(Qt.Orientation.Horizontal)
            self.scrubber.setRange(0, 1)
            self.scrubber.setEnabled(False)
            self.scrubber.sliderMoved.connect(self._seek_from_slider)
            self.scrubber.sliderPressed.connect(self._slider_pressed)
            self.scrubber.sliderReleased.connect(self._slider_released)
            playback.addWidget(self.scrubber, stretch=1)
            layout.addLayout(playback)

            editor = QGridLayout()
            editor.addWidget(QLabel("Start (HH:MM:SS.mmm)"), 0, 0)
            self.start_edit = QLineEdit()
            editor.addWidget(self.start_edit, 0, 1)
            self.use_start_button = QPushButton("Use playhead")
            self.use_start_button.clicked.connect(lambda: self._set_field_to_position(self.start_edit))
            self.use_start_button.setEnabled(False)
            editor.addWidget(self.use_start_button, 0, 2)
            editor.addWidget(QLabel("End"), 0, 3)
            self.end_edit = QLineEdit()
            editor.addWidget(self.end_edit, 0, 4)
            self.use_end_button = QPushButton("Use playhead")
            self.use_end_button.clicked.connect(lambda: self._set_field_to_position(self.end_edit))
            self.use_end_button.setEnabled(False)
            editor.addWidget(self.use_end_button, 0, 5)
            editor.addWidget(QLabel("Comment (optional)"), 1, 0)
            self.comment_edit = QPlainTextEdit()
            self.comment_edit.setMaximumHeight(72)
            editor.addWidget(self.comment_edit, 1, 1, 1, 5)

            actions = QHBoxLayout()
            self.add_button = QPushButton("Add range")
            self.add_button.clicked.connect(self.add_range)
            self.add_button.setEnabled(False)
            actions.addWidget(self.add_button)
            self.update_button = QPushButton("Update selected")
            self.update_button.clicked.connect(self.update_range)
            self.update_button.setEnabled(False)
            actions.addWidget(self.update_button)
            self.remove_button = QPushButton("Remove selected")
            self.remove_button.clicked.connect(self.remove_range)
            self.remove_button.setEnabled(False)
            actions.addWidget(self.remove_button)
            clear_button = QPushButton("Clear fields")
            clear_button.clicked.connect(self.clear_fields)
            actions.addWidget(clear_button)
            editor.addLayout(actions, 0, 6, 2, 1)
            for col in (1, 4):
                editor.setColumnStretch(col, 1)
            layout.addLayout(editor)

            self.table = QTableWidget(0, 4)
            self.table.setHorizontalHeaderLabels(("ID", "Start", "End", "Comment"))
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemSelectionChanged.connect(self.on_select_range)
            layout.addWidget(self.table)

            bottom = QGridLayout()
            self.status_label = QLabel("Load a video to identify intervals.")
            self.status_label.setWordWrap(True)
            bottom.addWidget(self.status_label, 0, 0, 1, 3)
            self.output_path_label = QLabel(f"CSV: {self.output_path}")
            self.output_path_label.setWordWrap(True)
            self.output_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            bottom.addWidget(self.output_path_label, 1, 0)
            self.browse_csv_button = QPushButton("Browse CSV…")
            self.browse_csv_button.clicked.connect(self.browse_output_csv)
            self.browse_csv_button.setEnabled(False)
            bottom.addWidget(self.browse_csv_button, 1, 1)
            self.save_button = QPushButton("Save CSV")
            self.save_button.clicked.connect(self.save_csv)
            self.save_button.setEnabled(False)
            bottom.addWidget(self.save_button, 0, 3, 2, 1)
            bottom.setColumnStretch(0, 1)
            layout.addLayout(bottom)

        def _set_status(self, message: str) -> None:
            self.status_label.setText(message)

        def browse_video(self) -> None:
            selected, _ = QFileDialog.getOpenFileName(
                self, "Select stitched video", "", QT_VIDEO_FILTER,
            )
            if not selected:
                return
            try:
                candidate = probe_video(self.ffprobe, Path(selected))
            except StitchingError as exc:
                QMessageBox.critical(self, "Could not open video", str(exc))
                return
            if not self._confirm_discard_or_save("loading another video"):
                return
            self.load_video(candidate)

        def load_video(self, video: VideoInfo) -> None:
            self.player.stop()
            self.media_ready = False
            self.video = video
            self.duration = 0.0
            self.output_path = self.default_output_path
            self.output_path_label.setText(f"CSV: {self.output_path}")
            self.ranges.clear()
            self.next_uid = 1
            self.selected_uid = None
            self.dirty = False
            self._refresh_ranges()
            self.start_edit.clear()
            self.end_edit.clear()
            self.comment_edit.clear()
            self.path_label.setText(str(video.path))
            self.info_label.setText(
                f"Source: {video.width}×{video.height} · {video.fps:.6g} fps · "
                f"{format_timestamp(video.duration)} · "
                f"{'audio available' if video.has_audio else 'no audio stream'} · Loading media…"
            )
            self.clock_label.setText("00:00:00.000 / Loading duration…")
            self._set_media_controls(False)
            self.scrubber.setRange(0, max(1, int(round(video.duration * 1000))))
            self.player.setSource(QUrl.fromLocalFile(str(video.path)))
            self._set_status("Loading video in Qt Multimedia…")

        def _set_media_controls(self, enabled: bool) -> None:
            self.play_button.setEnabled(enabled)
            self.stop_button.setEnabled(enabled)
            self.scrubber.setEnabled(enabled)
            self.add_button.setEnabled(enabled)
            self.use_start_button.setEnabled(enabled)
            self.use_end_button.setEnabled(enabled)
            self.save_button.setEnabled(enabled)
            self.browse_csv_button.setEnabled(enabled)

        def _media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
            if status == QMediaPlayer.MediaStatus.LoadedMedia:
                duration_ms = self.player.duration()
                if duration_ms <= 0:
                    self._set_status("Qt Multimedia loaded the file but did not report a valid duration.")
                    self._set_media_controls(False)
                    return
                self.duration = duration_ms / 1000.0
                self.media_ready = True
                self.scrubber.setRange(0, duration_ms)
                self._set_media_controls(True)
                if self.player.hasAudio():
                    audio = "audio available"
                elif self.video.has_audio:
                    audio = "source audio unavailable in Qt Multimedia"
                else:
                    audio = "no audio stream"
                self.info_label.setText(
                    f"Source: {self.video.width}×{self.video.height} · {self.video.fps:.6g} fps · "
                    f"{format_timestamp(self.duration)} · {audio}"
                )
                self.clock_label.setText(f"{format_timestamp(0)} / {format_timestamp(self.duration)}")
                if self.player.hasAudio():
                    self._set_status("Ready. Mark a start and end time, then add a range.")
                elif self.video.has_audio:
                    self._set_status(
                        "Qt Multimedia could not open the source audio track; video and range marking remain available."
                    )
                else:
                    self._set_status(
                        "This video has no audio stream; video playback and range marking are available."
                    )
            elif status == QMediaPlayer.MediaStatus.InvalidMedia:
                self.media_ready = False
                self._set_media_controls(False)
                self._set_status("Could not load video in Qt Multimedia. Choose another video or check its codecs.")
            elif status == QMediaPlayer.MediaStatus.EndOfMedia:
                self._position_changed(self.player.position())

        def _duration_changed(self, duration_ms: int) -> None:
            if duration_ms <= 0:
                return
            self.duration = duration_ms / 1000.0
            self.scrubber.setRange(0, duration_ms)
            self.clock_label.setText(
                f"{format_timestamp(self.player.position() / 1000.0)} / {format_timestamp(self.duration)}"
            )

        def _media_error(self, _error: QMediaPlayer.Error, error_string: str) -> None:
            if not error_string:
                return
            if self.player.hasVideo() and self.player.duration() > 0:
                self.media_ready = True
                self.duration = self.player.duration() / 1000.0
                self._set_media_controls(True)
                self._set_status(
                    f"Qt Multimedia reported a playback warning: {error_string}. "
                    "Video and interval marking remain available; audio may be unavailable."
                )
                return
            self.media_ready = False
            self._set_media_controls(False)
            self._set_status(f"Qt Multimedia playback error: {error_string}")
            QMessageBox.critical(self, "Video playback error", error_string)

        def _position_changed(self, position_ms: int) -> None:
            seconds = max(0.0, position_ms / 1000.0)
            self.clock_label.setText(f"{format_timestamp(seconds)} / {format_timestamp(self.duration)}")
            if not self.updating_slider:
                self.updating_slider = True
                self.scrubber.setValue(position_ms)
                self.updating_slider = False

        def _playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
            self.play_button.setText(
                "Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play"
            )

        def toggle_play(self) -> None:
            if not self.media_ready:
                return
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()

        def stop_play(self) -> None:
            if self.media_ready:
                self.player.stop()
                self.player.setPosition(0)
                self._position_changed(0)

        def _slider_pressed(self) -> None:
            self.updating_slider = True

        def _seek_from_slider(self, position_ms: int) -> None:
            if self.media_ready:
                self.player.setPosition(position_ms)
                self._position_changed(position_ms)

        def _slider_released(self) -> None:
            self.updating_slider = False
            if self.media_ready:
                self.player.setPosition(self.scrubber.value())

        def _set_field_to_position(self, field: QLineEdit) -> None:
            if self.media_ready:
                field.setText(format_timestamp(self.player.position() / 1000.0))

        def _confirm_discard_or_save(self, action: str) -> bool:
            if not self.dirty and not self.ranges:
                return True
            choice = QMessageBox.question(
                self,
                "Current ranges will be replaced",
                f"Save the current ranges before {action}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return False
            if choice == QMessageBox.StandardButton.Yes:
                return self.save_csv()
            return True

        def browse_output_csv(self) -> None:
            if not self.media_ready:
                QMessageBox.critical(self, "No video loaded", "Load a video before selecting a removal CSV.")
                return
            initial_dir = self.output_path.parent if self.output_path.parent.is_dir() else self.repository_root
            dialog = QFileDialog(self, "Select removal CSV", str(initial_dir / self.output_path.name))
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            dialog.setNameFilter("CSV files (*.csv)")
            dialog.setDefaultSuffix("csv")
            dialog.setOption(QFileDialog.Option.DontConfirmOverwrite, True)
            if dialog.exec() != QFileDialog.DialogCode.Accepted or not dialog.selectedFiles():
                return
            selected = dialog.selectedFiles()[0]
            candidate = Path(selected).expanduser().resolve()
            if candidate.suffix.lower() != ".csv":
                QMessageBox.critical(self, "Invalid output file", "Choose a file with the .csv extension.")
                return
            if candidate.exists():
                if not candidate.is_file():
                    QMessageBox.critical(self, "Invalid output file", f"Not a file: {candidate}")
                    return
                try:
                    imported = load_ranges(candidate, self.duration)
                except (OSError, ValueError) as exc:
                    QMessageBox.critical(self, "Could not load CSV", str(exc))
                    return
                if not self._confirm_discard_or_save("loading ranges from another CSV"):
                    return
                self.output_path = candidate
                self.output_path_label.setText(f"CSV: {self.output_path}")
                self.ranges = imported
                self.next_uid = len(imported) + 1
                self.selected_uid = None
                self._refresh_ranges()
                self.clear_fields()
                self.dirty = False
                self._set_status(f"Loaded {len(imported)} interval(s) from {candidate}")
                return
            self.output_path = candidate
            self.output_path_label.setText(f"CSV: {self.output_path}")
            self._set_status(f"New CSV destination selected: {candidate}")

        def add_range(self) -> None:
            self._create_or_update(None)

        def update_range(self) -> None:
            self._create_or_update(self.selected_uid)

        def _create_or_update(self, uid: int | None) -> None:
            if not self.media_ready:
                return
            try:
                start = parse_timestamp(self.start_edit.text())
                end = parse_timestamp(self.end_edit.text())
            except ValueError as exc:
                QMessageBox.critical(self, "Invalid time", str(exc))
                return
            if not (0 <= start < end <= self.duration):
                QMessageBox.critical(
                    self, "Invalid interval",
                    f"Require 0 ≤ start < end ≤ video duration ({format_timestamp(self.duration)}).",
                )
                return
            for existing in self.ranges:
                if existing.uid != uid and start < existing.end and existing.start < end:
                    QMessageBox.critical(
                        self, "Overlapping interval",
                        f"This interval overlaps {format_timestamp(existing.start)}–{format_timestamp(existing.end)}. "
                        "Removal intervals must not overlap.",
                    )
                    return
            comment = self.comment_edit.toPlainText()
            if uid is None:
                created_uid = self.next_uid
                self.ranges.append(RemovalRange(created_uid, start, end, comment))
                self.next_uid += 1
            else:
                created_uid = uid
                existing = next((item for item in self.ranges if item.uid == uid), None)
                if existing is None:
                    QMessageBox.critical(self, "Selection changed", "Select a range and try again.")
                    return
                existing.start, existing.end, existing.comment = start, end, comment
            self.ranges.sort(key=lambda item: (item.start, item.end))
            self.dirty = True
            self._refresh_ranges(select_uid=created_uid)
            self._set_status("Range updated. Save CSV to keep these changes.")

        def _refresh_ranges(self, select_uid: int | None = None) -> None:
            self.table.setRowCount(0)
            ordered = sorted(self.ranges, key=lambda item: (item.start, item.end))
            for index, item in enumerate(ordered, start=1):
                row = self.table.rowCount()
                self.table.insertRow(row)
                values = (str(index), format_timestamp(item.start), format_timestamp(item.end), item.comment)
                for col, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    if col == 0:
                        cell.setData(Qt.ItemDataRole.UserRole, item.uid)
                    self.table.setItem(row, col, cell)
            self.selected_uid = None
            if select_uid is not None:
                for row in range(self.table.rowCount()):
                    if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == select_uid:
                        self.table.selectRow(row)
                        self.selected_uid = select_uid
                        break
            self.update_button.setEnabled(self.selected_uid is not None)
            self.remove_button.setEnabled(self.selected_uid is not None)

        def on_select_range(self) -> None:
            selected = self.table.selectedItems()
            if not selected:
                self.selected_uid = None
                self.update_button.setEnabled(False)
                self.remove_button.setEnabled(False)
                return
            row = selected[0].row()
            uid = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            item = next((entry for entry in self.ranges if entry.uid == uid), None)
            if item is None:
                return
            self.selected_uid = item.uid
            self.start_edit.setText(format_timestamp(item.start))
            self.end_edit.setText(format_timestamp(item.end))
            self.comment_edit.setPlainText(item.comment)
            self.update_button.setEnabled(True)
            self.remove_button.setEnabled(True)

        def remove_range(self) -> None:
            if self.selected_uid is None:
                return
            self.ranges = [item for item in self.ranges if item.uid != self.selected_uid]
            self.selected_uid = None
            self.dirty = True
            self._refresh_ranges()
            self.clear_fields()
            self._set_status("Range removed. Save CSV to keep these changes.")

        def clear_fields(self) -> None:
            self.start_edit.clear()
            self.end_edit.clear()
            self.comment_edit.clear()
            self.selected_uid = None
            self.table.clearSelection()
            self.update_button.setEnabled(False)
            self.remove_button.setEnabled(False)

        def save_csv(self) -> bool:
            if not self.media_ready:
                QMessageBox.critical(self, "No video loaded", "Load a video before saving intervals.")
                return False
            try:
                save_ranges(self.output_path, self.ranges)
            except OSError as exc:
                QMessageBox.critical(self, "Could not save CSV", f"Destination: {self.output_path}\n\n{exc}")
                return False
            self.dirty = False
            self._set_status(f"Saved {len(self.ranges)} interval(s) to {self.output_path}")
            return True

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method name.
            if self.dirty:
                choice = QMessageBox.question(
                    self, "Unsaved removal ranges", "Save removal ranges before closing?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if choice == QMessageBox.StandardButton.Cancel:
                    event.ignore()
                    return
                if choice == QMessageBox.StandardButton.Yes and not self.save_csv():
                    event.ignore()
                    return
            self.player.stop()
            event.accept()
else:
    class RemovalEditor:
        """Placeholder used only when PySide6 is unavailable."""

        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("PySide6 is required to create the Step 08 GUI.")


def main(repository_root: Path | None = None, argv: list[str] | None = None) -> int:
    root_path = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    parser = argparse.ArgumentParser(description="Review a stitched video and mark intervals for later removal.")
    parser.add_argument("--video", help="Video to load (relative paths resolve from the repository root).")
    args = parser.parse_args(argv)
    if QT_IMPORT_ERROR is not None:
        print(
            "Step 08 requires PySide6 Qt Multimedia. Activate the VideoStitcher environment and install it with "
            "`conda install -n VideoStitcher --override-channels -c conda-forge opencv=4.13.0 pyside6 qt6-multimedia`.",
            file=sys.stderr,
        )
        return 1
    try:
        settings = load_settings(root_path)
        ffprobe = _ffprobe_tool()
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
        default_info = None
        label = "specified" if args.video else "default"
        startup_error = f"Could not load {label} video {default_video}: {exc} Use Browse video… to select a file."

    app = QApplication.instance() or QApplication(sys.argv[:1])
    editor = RemovalEditor(settings, root_path, ffprobe)
    if default_info is not None:
        editor.load_video(default_info)
    elif startup_error:
        editor._set_status(startup_error)
    editor.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
