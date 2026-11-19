"""Select and save a rectangular region directly from a video preview."""

from __future__ import annotations

import argparse
import base64
import json
import math
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Sequence, Any
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np

from .workflow_paths import results_directory, scenario_prefix


class Step09aError(RuntimeError):
    """An actionable video, settings, or ROI record error."""


def load_settings(path: Path, repository_root: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise Step09aError(f"Settings file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Step09aError(f"Settings file is not valid JSON ({path}): {exc}") from exc
    except OSError as exc:
        raise Step09aError(f"Could not read settings file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise Step09aError("Settings file must contain a JSON object.")
    scenario = payload.get("ScenarioName")
    if not isinstance(scenario, str) or not scenario.strip():
        raise Step09aError("Set a non-empty ScenarioName in the selected settings file.")
    try:
        scenario_prefix(scenario)
        results_directory(payload, repository_root)
    except (TypeError, ValueError) as exc:
        raise Step09aError(str(exc)) from exc
    return payload


def resolve_path(value: str | Path, repository_root: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else repository_root / path).resolve()


class RegionOfInterestApp:
    def __init__(
        self,
        root: tk.Tk,
        repository_root: Path,
        settings_path: Path,
        settings: dict[str, Any],
        video_path: Path,
    ) -> None:
        self.root = root
        self.repository_root = repository_root
        self.settings_path = settings_path
        self.settings = settings
        self.capture: cv2.VideoCapture | None = None
        self.video_path: Path | None = None
        self.video_fps = 0.0
        self.video_duration = 0.0
        self.current_time = 0.0
        self.current_frame: np.ndarray | None = None
        self.selection_time: float | None = None
        self.top_left: tuple[int, int] | None = None
        self.bottom_right: tuple[int, int] | None = None
        self.display_box: tuple[float, float, float, float] | None = None
        self.dragging_corner: str | None = None
        self.photo: tk.PhotoImage | None = None
        self.playing = False
        self.after_id: str | None = None
        self.updating_slider = False

        root.title("Video Stitcher - Identify Region of Interest")
        root.minsize(900, 650)
        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._set_settings(settings_path, settings)
        self.open_video(video_path)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=10)
        container.pack(fill="both", expand=True)
        container.rowconfigure(2, weight=1)
        container.columnconfigure(0, weight=1)

        settings_row = ttk.Frame(container)
        settings_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        settings_row.columnconfigure(2, weight=1)
        ttk.Button(settings_row, text="Browse settings...", command=self.browse_settings).grid(row=0, column=0)
        ttk.Label(settings_row, text="Settings JSON:").grid(row=0, column=1, padx=(8, 6))
        self.settings_path_var = tk.StringVar()
        ttk.Label(settings_row, textvariable=self.settings_path_var, anchor="w").grid(
            row=0, column=2, sticky="ew"
        )

        video_row = ttk.Frame(container)
        video_row.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        video_row.columnconfigure(1, weight=1)
        ttk.Button(video_row, text="Browse video...", command=self.browse_video).grid(row=0, column=0)
        self.video_path_var = tk.StringVar(value="No video selected")
        ttk.Label(video_row, textvariable=self.video_path_var, anchor="w").grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )

        self.canvas = tk.Canvas(container, background="#171717", highlightthickness=0)
        self.canvas.grid(row=2, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self.render)
        self.canvas.bind("<Button-1>", self.canvas_press)
        self.canvas.bind("<B1-Motion>", self.canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.canvas_release)

        controls = ttk.Frame(container)
        controls.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.play_button = ttk.Button(controls, text="Play", command=self.toggle_play)
        self.play_button.pack(side="left")
        ttk.Button(controls, text="Stop", command=self.stop).pack(side="left", padx=(5, 8))
        self.time_var = tk.DoubleVar(value=0.0)
        self.seek_scale = ttk.Scale(
            controls, from_=0, to=1, variable=self.time_var, command=self._slider_changed
        )
        self.seek_scale.pack(side="left", fill="x", expand=True, padx=6)
        self.seek_scale.bind("<ButtonRelease-1>", self.seek_to_slider)
        self.clock_var = tk.StringVar(value="00:00:00.000 / 00:00:00.000")
        ttk.Label(controls, textvariable=self.clock_var, width=28, anchor="e").pack(side="right", padx=6)
        actions = ttk.Frame(container)
        actions.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.clear_button = ttk.Button(actions, text="Clear selection", command=self.clear_roi)
        self.clear_button.pack(side="left")
        self.save_button = ttk.Button(actions, text="Save ROI...", command=self.save_roi, state="disabled")
        self.save_button.pack(side="right")
        self.status_var = tk.StringVar(value="Seek to the desired frame, then click its top-left ROI corner.")
        ttk.Label(container, textvariable=self.status_var, anchor="w", wraplength=1000).grid(
            row=5, column=0, sticky="ew", pady=(6, 0)
        )

    def _set_settings(self, path: Path, settings: dict[str, Any]) -> None:
        self.settings_path = path
        self.settings = settings
        self.settings_path_var.set(str(path))
        self.root.title(f"Video Stitcher - ROI: {settings['ScenarioName']}")

    def browse_settings(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select settings JSON",
            initialdir=str(self.settings_path.parent),
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            parent=self.root,
        )
        if not selected:
            return
        path = Path(selected).expanduser().resolve()
        try:
            settings = load_settings(path, self.repository_root)
        except Step09aError as exc:
            messagebox.showerror("Invalid settings file", str(exc), parent=self.root)
            return
        self._set_settings(path, settings)
        self._set_status(f"ROI records will be saved under {results_directory(settings, self.repository_root)}")

    def browse_video(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Step 08b video",
            filetypes=(("Video files", "*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mts *.m2ts"), ("All files", "*.*")),
            parent=self.root,
        )
        if selected:
            self.open_video(Path(selected).expanduser().resolve())

    def open_video(self, path: Path) -> None:
        new_capture = cv2.VideoCapture(str(path))
        if not new_capture.isOpened():
            new_capture.release()
            messagebox.showerror("Could not open video", f"OpenCV could not open:\n{path}", parent=self.root)
            return
        fps = float(new_capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(new_capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(new_capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(new_capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = frame_count / fps if math.isfinite(fps) and fps > 0 and frame_count > 0 else 0.0
        if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0 or not math.isfinite(duration):
            new_capture.release()
            messagebox.showerror(
                "Invalid video", f"Could not determine a positive duration, frame rate, and dimensions for:\n{path}",
                parent=self.root,
            )
            return
        self.pause()
        if self.capture is not None:
            self.capture.release()
        self.capture = new_capture
        self.video_path = path
        self.video_fps = fps
        self.video_duration = duration
        self.video_path_var.set(str(path))
        self.selection_time = None
        self.seek_scale.configure(to=max(0.0, duration - 1.0 / fps))
        self.time_var.set(0.0)
        self._decode_at(0.0)
        self.clear_roi()
        self._set_status(f"Loaded {width}x{height} video at {fps:.6g} fps ({duration:.3f}s). Seek and click the video preview to select the ROI.")

    def _decode_at(self, seconds: float) -> bool:
        if self.capture is None or self.video_fps <= 0:
            return False
        frame_index = int(round(max(0.0, min(seconds, self.video_duration)) * self.video_fps))
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = self.capture.read()
        if not ok or frame is None:
            self._set_status(f"Could not decode a frame near {seconds:.3f}s.")
            return False
        self.current_frame = frame
        self.current_time = frame_index / self.video_fps
        self.updating_slider = True
        self.time_var.set(self.current_time)
        self.updating_slider = False
        self._update_clock()
        self.render()
        return True

    def _slider_changed(self, value: str) -> None:
        if self.updating_slider:
            return
        seconds = float(value)
        self.clock_var.set(f"{self._format_time(seconds)} / {self._format_time(self.video_duration)}")

    def seek_to_slider(self, _event=None) -> None:
        if self.playing:
            self.pause()
        if not self._decode_at(self.time_var.get()):
            self._set_status("Seek failed; the last successfully decoded frame is still available.")
        else:
            self._set_status(f"Preview at {self._format_time(self.current_time)}. Click the video preview to select the ROI.")

    def toggle_play(self) -> None:
        if self.playing:
            self.pause()
            return
        if self.capture is None:
            return
        self.playing = True
        self.play_button.configure(text="Pause")
        self._play_tick()

    def _play_tick(self) -> None:
        if not self.playing or self.capture is None:
            return
        ok, frame = self.capture.read()
        if not ok or frame is None:
            self.pause()
            return
        self.current_frame = frame
        frame_number = max(0, int(self.capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1)
        self.current_time = frame_number / self.video_fps
        self.updating_slider = True
        self.time_var.set(self.current_time)
        self.updating_slider = False
        self._update_clock()
        self.render()
        delay = max(1, int(round(1000 / self.video_fps)))
        self.after_id = self.root.after(delay, self._play_tick)

    def pause(self) -> None:
        self.playing = False
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        if hasattr(self, "play_button"):
            self.play_button.configure(text="Play")

    def stop(self) -> None:
        self.pause()
        if self.capture is not None:
            self._decode_at(0.0)
            self._set_status("Stopped at the beginning of the video.")

    def clear_roi(self) -> None:
        self.dragging_corner = None
        self.top_left = None
        self.bottom_right = None
        self.selection_time = None
        self.save_button.configure(state="disabled")
        self.render()
        self._set_status("ROI selection cleared. Seek to the desired frame, then click its top-left corner.")

    def canvas_click(self, event) -> None:
        if (
            self.current_frame is None or self.display_box is None
            or self.bottom_right is not None
        ):
            return
        left, top, shown_width, shown_height = self.display_box
        if not (left <= event.x < left + shown_width and top <= event.y < top + shown_height):
            return
        # Freeze playback on an actual image click; no separate capture step is
        # needed. The first corner's playhead time is kept as selection metadata.
        self.pause()
        if self.top_left is None:
            self.selection_time = self.current_time
        height, width = self.current_frame.shape[:2]
        x = min(width - 1, max(0, int((event.x - left) * width / shown_width)))
        y = min(height - 1, max(0, int((event.y - top) * height / shown_height)))
        if self.top_left is None:
            self.top_left = (x, y)
            self._set_status(
                f"Top-left corner: ({x}, {y}) at "
                f"{self._format_time(self.selection_time if self.selection_time is not None else self.current_time)}. "
                "Now click the bottom-right corner."
            )
        elif x <= self.top_left[0] or y <= self.top_left[1]:
            self._set_status("Bottom-right must be to the right of and below the top-left corner. Try again.")
            return
        else:
            self.bottom_right = (x, y)
            valid, expanded_axes = self._normalize_roi_dimensions()
            self.save_button.configure(state="normal" if valid else "disabled")
            if valid:
                adjustment = f"; expanded by one pixel on {', '.join(expanded_axes)}" if expanded_axes else ""
                self._set_status(f"ROI selected: {self.top_left} to {self.bottom_right}{adjustment}. Save when ready.")
            else:
                self._set_status(
                    "The ROI width or height is odd and cannot be expanded inside the frame. "
                    "Drag a corner to make that dimension even."
                )
        self.render()

    def canvas_press(self, event) -> None:
        """Start dragging a completed ROI corner, or handle a new corner click."""
        self.dragging_corner = None
        if (
            self.top_left is not None and self.bottom_right is not None
            and self.display_box is not None
        ):
            # Pick whichever control point is nearest to the pointer, provided it
            # is close enough to feel like grabbing the visible marker.
            marker_radius = 12
            candidates = []
            for name, point in (("top_left", self.top_left), ("bottom_right", self.bottom_right)):
                px, py = self._display_point(point)
                distance_squared = (event.x - px) ** 2 + (event.y - py) ** 2
                if distance_squared <= marker_radius**2:
                    candidates.append((distance_squared, name))
            if candidates:
                self.pause()
                self.dragging_corner = min(candidates)[1]
                self.canvas.configure(cursor="fleur")
                return
        self.canvas_click(event)

    def canvas_drag(self, event) -> None:
        if self.dragging_corner is None or self.current_frame is None or self.display_box is None:
            return
        left, top, shown_width, shown_height = self.display_box
        if not (left <= event.x < left + shown_width and top <= event.y < top + shown_height):
            return
        height, width = self.current_frame.shape[:2]
        x = min(width - 1, max(0, int((event.x - left) * width / shown_width)))
        y = min(height - 1, max(0, int((event.y - top) * height / shown_height)))
        if self.dragging_corner == "top_left":
            assert self.bottom_right is not None
            x = min(x, self.bottom_right[0] - 1)
            y = min(y, self.bottom_right[1] - 1)
            self.top_left = (x, y)
        else:
            assert self.top_left is not None
            x = max(x, self.top_left[0] + 1)
            y = max(y, self.top_left[1] + 1)
            x = min(width - 1, x)
            y = min(height - 1, y)
            self.bottom_right = (x, y)
        self.save_button.configure(state="disabled")
        self.render()
        self._set_status(f"ROI adjusted: {self.top_left} to {self.bottom_right}. Drag either marker to refine it.")

    def canvas_release(self, _event=None) -> None:
        valid = False
        if self.current_frame is not None and self.top_left is not None and self.bottom_right is not None:
            valid, expanded_axes = self._normalize_roi_dimensions()
            if valid:
                self.save_button.configure(state="normal")
                if expanded_axes:
                    self._set_status(
                        f"ROI adjusted: {self.top_left} to {self.bottom_right}; "
                        f"expanded by one pixel on {', '.join(expanded_axes)}. Save when ready."
                    )
                else:
                    self._set_status(f"ROI adjusted: {self.top_left} to {self.bottom_right}. Save when ready.")
            else:
                self.save_button.configure(state="disabled")
                self._set_status(
                    "The ROI width or height is odd and cannot be expanded inside the frame. "
                    "Drag a corner to make that dimension even."
                )
        self.dragging_corner = None
        self.canvas.configure(cursor="")
        self.render()

    def _normalize_roi_dimensions(self) -> tuple[bool, list[str]]:
        """Expand odd ROI dimensions by one pixel without exceeding the frame."""
        if self.current_frame is None or self.top_left is None or self.bottom_right is None:
            return False, []
        height, width = self.current_frame.shape[:2]
        left, top = self.top_left
        right, bottom = self.bottom_right
        expanded_axes: list[str] = []

        if (right - left + 1) % 2:
            if right < width - 1:
                right += 1
                expanded_axes.append("right")
            elif left > 0:
                left -= 1
                expanded_axes.append("left")
            else:
                return False, []

        if (bottom - top + 1) % 2:
            if bottom < height - 1:
                bottom += 1
                expanded_axes.append("bottom")
            elif top > 0:
                top -= 1
                expanded_axes.append("top")
            else:
                return False, []

        self.top_left = (left, top)
        self.bottom_right = (right, bottom)
        return True, expanded_axes

    def render(self, _event=None) -> None:
        self.canvas.delete("all")
        self.display_box = None
        self.photo = None
        image = self.current_frame
        if image is None:
            self.canvas.create_text(
                max(1, self.canvas.winfo_width() // 2),
                max(1, self.canvas.winfo_height() // 2),
                text="Open a video, seek to a frame, then click the preview to select the ROI.", fill="white",
            )
            return
        canvas_width, canvas_height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        height, width = image.shape[:2]
        scale = min(canvas_width / width, canvas_height / height)
        shown_width, shown_height = max(1, round(width * scale)), max(1, round(height * scale))
        left, top = (canvas_width - shown_width) / 2, (canvas_height - shown_height) / 2
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_NEAREST
        resized = cv2.resize(image, (shown_width, shown_height), interpolation=interpolation)
        ok, png = cv2.imencode(".png", resized)
        if not ok:
            self._set_status("Could not render the current video frame.")
            return
        self.photo = tk.PhotoImage(master=self.canvas, data=base64.b64encode(png.tobytes()).decode("ascii"))
        self.canvas.create_image(left, top, image=self.photo, anchor="nw")
        self.display_box = (left, top, shown_width, shown_height)
        if self.top_left is not None:
            self._draw_marker(self.top_left, "#00ff66")
        if self.bottom_right is not None and self.top_left is not None:
            self._draw_marker(self.bottom_right, "#ff5a5f")
            radius = 5
            x1, y1 = self._display_point(self.top_left)
            x2, y2 = self._display_point(self.bottom_right)
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#00ff66", width=2)

    def _display_point(self, point: tuple[int, int]) -> tuple[float, float]:
        assert self.display_box is not None and self.current_frame is not None
        left, top, shown_width, shown_height = self.display_box
        height, width = self.current_frame.shape[:2]
        return left + (point[0] + 0.5) * shown_width / width, top + (point[1] + 0.5) * shown_height / height

    def _draw_marker(self, point: tuple[int, int], color: str) -> None:
        x, y = self._display_point(point)
        radius = 5
        self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline="white", width=2, fill=color)

    def save_roi(self) -> None:
        if (
            self.video_path is None or self.current_frame is None or self.selection_time is None
            or self.top_left is None or self.bottom_right is None
        ):
            messagebox.showwarning("ROI incomplete", "Select both ROI corners directly on the video preview before saving.", parent=self.root)
            return
        roi_width = self.bottom_right[0] - self.top_left[0] + 1
        roi_height = self.bottom_right[1] - self.top_left[1] + 1
        if roi_width % 2 or roi_height % 2:
            self.save_button.configure(state="disabled")
            messagebox.showwarning(
                "ROI dimensions must be even",
                "The ROI width and height must both be divisible by 2. Adjust a corner and try again.",
                parent=self.root,
            )
            return
        try:
            scenario_value = self.settings.get("ScenarioName")
            if not isinstance(scenario_value, str) or not scenario_value.strip():
                raise Step09aError("Selected settings file has no valid ScenarioName.")
            prefix = scenario_prefix(scenario_value)
            output_dir = results_directory(self.settings, self.repository_root) / "Step09a_IdentifyRegionOfInterest"
            output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            output_path = output_dir / f"{prefix}_RegionOfInterest_{timestamp}.json"
            height, width = self.current_frame.shape[:2]
            record = {
                "ScenarioName": scenario_value,
                "CreatedAtLocal": datetime.now().astimezone().isoformat(timespec="seconds"),
                "SourceVideoPath": str(self.video_path),
                "FrameTime_s": self.selection_time,
                "FrameWidth_px": width,
                "FrameHeight_px": height,
                "RegionOfInterest": {
                    "TopLeft": {"x": self.top_left[0], "y": self.top_left[1]},
                    "BottomRight": {"x": self.bottom_right[0], "y": self.bottom_right[1]},
                    "Width_px": roi_width,
                    "Height_px": roi_height,
                },
            }
            self._write_json_atomically(output_path, record)
        except (OSError, TypeError, ValueError, Step09aError) as exc:
            messagebox.showerror("Could not save ROI", f"Could not save the ROI record.\n\n{exc}", parent=self.root)
            return
        self._set_status(f"Saved ROI record to {output_path}")
        messagebox.showinfo("ROI saved", f"Saved ROI record to:\n{output_path}", parent=self.root)

    @staticmethod
    def _write_json_atomically(path: Path, record: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(record, handle, indent=2)
                handle.write("\n")
            temporary.replace(path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _update_clock(self) -> None:
        self.clock_var.set(
            f"{self._format_time(self.current_time)} / {self._format_time(self.video_duration)}"
        )

    @staticmethod
    def _format_time(seconds: float) -> str:
        milliseconds = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        whole_seconds, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{millis:03d}"

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def close(self) -> None:
        self.pause()
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        self.root.destroy()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select a rectangular ROI from a Step 08b video frame.")
    parser.add_argument("--settings", type=Path, help="Settings JSON; relative paths resolve from repository root.")
    parser.add_argument("--video", type=Path, help="Source video; defaults to the configured Step 08b output.")
    return parser.parse_args(argv)


def main(repository_root: Path | None = None, argv: Sequence[str] | None = None) -> int:
    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    args = _parse_args(argv)
    settings_path = resolve_path(args.settings or Path("settings.json"), root)
    try:
        settings = load_settings(settings_path, root)
        scenario = scenario_prefix(settings["ScenarioName"])
        video_path = resolve_path(args.video, root) if args.video else (
            results_directory(settings, root) / "Step08b_RemovedSections" / f"{scenario}_SectionsRemoved.mp4"
        ).resolve()
    except (Step09aError, OSError, ValueError) as exc:
        print(f"Step 09a could not start: {exc}")
        return 1

    root_window = tk.Tk()
    app = RegionOfInterestApp(root_window, root, settings_path, settings, video_path)
    try:
        root_window.state("zoomed")
    except tk.TclError:
        root_window.attributes("-zoomed", True)
    root_window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
