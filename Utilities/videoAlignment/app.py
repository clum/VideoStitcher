"""Tkinter interface for manually aligning two video and audio recordings."""

import base64
import subprocess
import time
import tkinter as tk
from fractions import Fraction
from tkinter import filedialog, messagebox, ttk

from .alignment import Alignment, calculate_alignment
from .media import MediaInfo, frame_png, probe, require_tools


def _seconds(value: Fraction) -> str:
    return f"{float(value):.6f}"


def _clock(value: Fraction) -> str:
    sign = "−" if value < 0 else ""
    millis = int(abs(value) * 1000)
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, ms = divmod(remainder, 1000)
    return f"{sign}{hours:02}:{minutes:02}:{seconds:02}.{ms:03}"


class VideoStitcherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Video Stitcher — Manual Alignment")
        root.minsize(1000, 760)
        self.media: list[MediaInfo | None] = [None, None]
        self.photos: list[tk.PhotoImage | None] = [None, None]
        self.offset = Fraction(0)
        self.cursor = Fraction(0)
        self.dragging = False
        self.audio_processes: list[subprocess.Popen] = []
        self.playing = False
        self.play_started = 0.0
        self.play_start_cursor = Fraction(0)
        self.play_end = Fraction(0)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        ttk.Button(top, text="Choose reference video…", command=lambda: self.choose(0)).pack(side="left")
        ttk.Button(top, text="Choose second video…", command=lambda: self.choose(1)).pack(side="left", padx=(8, 0))
        self.file_labels = [ttk.Label(top, text="Reference: not selected"), ttk.Label(top, text="Second: not selected")]
        self.file_labels[0].pack(side="left", padx=(16, 0))
        self.file_labels[1].pack(side="left", padx=(12, 0))

        previews = ttk.Frame(self.root, padding=(10, 0, 10, 4))
        previews.pack(fill="x")
        self.preview_labels: list[tk.Label] = []
        for index, title in enumerate(("A · Reference", "B · Aligned second video")):
            pane = ttk.LabelFrame(previews, text=title, padding=6)
            pane.pack(side="left", fill="both", expand=True, padx=(0, 6) if index == 0 else (6, 0))
            preview_box = tk.Frame(pane, width=520, height=292, background="#171717")
            preview_box.pack()
            preview_box.pack_propagate(False)
            image = tk.Label(preview_box, text="Choose a video to preview", anchor="center", background="#171717", foreground="white")
            image.pack(fill="both", expand=True)
            self.preview_labels.append(image)
        self.info_labels = [ttk.Label(previews.winfo_children()[i], text="") for i in range(2)]
        for info_label, pane in zip(self.info_labels, previews.winfo_children()):
            info_label.pack(anchor="w", pady=(4, 0))

        timeline_frame = ttk.LabelFrame(self.root, text="Shared timeline · drag the second clip to align", padding=10)
        timeline_frame.pack(fill="x", padx=10, pady=6)
        self.timeline = tk.Canvas(timeline_frame, height=100, background="#f4f5f7", highlightthickness=0)
        self.timeline.pack(fill="x")
        self.timeline.bind("<Configure>", lambda _event: self.draw_timeline())
        self.timeline.bind("<Button-1>", self.timeline_click)
        self.timeline.bind("<B1-Motion>", self.timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", lambda _event: setattr(self, "dragging", False))
        self.timeline.bind("<Left>", lambda _event: self.nudge(Fraction(-1, 10)))
        self.timeline.bind("<Right>", lambda _event: self.nudge(Fraction(1, 10)))
        self.timeline.bind("<Shift-Left>", lambda _event: self.nudge(Fraction(-1, 100)))
        self.timeline.bind("<Shift-Right>", lambda _event: self.nudge(Fraction(1, 100)))

        controls = ttk.Frame(self.root, padding=(10, 2))
        controls.pack(fill="x")
        self.play_button = ttk.Button(controls, text="Play both audio tracks", command=self.toggle_play)
        self.play_button.pack(side="left")
        ttk.Button(controls, text="Stop", command=self.stop_playback).pack(side="left", padx=5)
        ttk.Label(controls, text="Timeline position:").pack(side="left", padx=(18, 4))
        self.cursor_var = tk.DoubleVar(value=0)
        self.scrubber = ttk.Scale(controls, from_=0, to=1, variable=self.cursor_var, command=self.scrubbed)
        self.scrubber.pack(side="left", fill="x", expand=True, padx=6)
        self.clock_label = ttk.Label(controls, text="00:00:00.000")
        self.clock_label.pack(side="left", padx=6)

        align_frame = ttk.LabelFrame(self.root, text="Alignment result", padding=10)
        align_frame.pack(fill="x", padx=10, pady=6)
        self.offset_label = ttk.Label(align_frame, text="Offset to apply to second video: +0.000000 s", font=("Segoe UI", 11, "bold"))
        self.offset_label.pack(anchor="w")
        self.result_label = ttk.Label(align_frame, text="Choose both videos to calculate shared trims.", justify="left")
        self.result_label.pack(anchor="w", pady=(6, 0))
        self.copy_button = ttk.Button(align_frame, text="Copy offset", command=self.copy_offset)
        self.copy_button.pack(anchor="e", pady=(4, 0))
        self.status = ttk.Label(self.root, text="Ready. FFmpeg, ffprobe, and ffplay must be available on PATH.", padding=(10, 4))
        self.status.pack(fill="x", side="bottom")

    def choose(self, index: int) -> None:
        path = filedialog.askopenfilename(title="Select video", filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mts *.m2ts"), ("All files", "*.*")])
        if not path:
            return
        try:
            info = probe(path)
        except Exception as exc:
            messagebox.showerror("Could not open video", str(exc), parent=self.root)
            return
        self.media[index] = info
        self.file_labels[index].configure(text=f"{'Reference' if index == 0 else 'Second'}: {info.path.name}")
        rate = info.frame_rate or "unknown"
        self.info_labels[index].configure(text=f"{info.width}×{info.height} · {float(info.duration):.3f} s · {rate} fps · {'audio' if info.has_audio else 'no audio'}")
        if not info.has_audio:
            messagebox.showwarning("No audio track", f"{info.path.name} has no audio stream. Its video can still be aligned visually.", parent=self.root)
        if index == 1:
            self.offset = Fraction(0)
        self.refresh()

    def duration(self, index: int) -> Fraction:
        info = self.media[index]
        return info.duration if info else Fraction(0)

    def bounds(self) -> tuple[Fraction, Fraction]:
        a, b = self.media
        if not a or not b:
            return Fraction(0), max(self.duration(0), self.duration(1), Fraction(1))
        start = min(Fraction(0), self.offset)
        end = max(a.duration, self.offset + b.duration)
        return start, max(end, start + Fraction(1, 100))

    def timeline_x(self, value: Fraction) -> float:
        start, end = self.bounds()
        width = max(1, self.timeline.winfo_width() - 30)
        return 15 + float((value - start) / (end - start)) * width

    def x_timeline(self, x: float) -> Fraction:
        start, end = self.bounds()
        width = max(1, self.timeline.winfo_width() - 30)
        return start + Fraction(str(max(0, min(1, (x - 15) / width)))) * (end - start)

    def draw_timeline(self) -> None:
        if not hasattr(self, "timeline"):
            return
        canvas = self.timeline
        canvas.delete("all")
        start, end = self.bounds()
        width = max(1, canvas.winfo_width() - 30)
        for index, y in ((0, 30), (1, 68)):
            info = self.media[index]
            if not info:
                continue
            clip_start = Fraction(0) if index == 0 else self.offset
            clip_end = clip_start + info.duration
            x1, x2 = self.timeline_x(clip_start), self.timeline_x(clip_end)
            color = "#4279c7" if index == 0 else "#36a179"
            canvas.create_text(3, y + 8, text="A" if index == 0 else "B", anchor="w", font=("Segoe UI", 9, "bold"))
            canvas.create_rectangle(x1, y, x2, y + 17, fill=color, outline="")
            if info.has_audio:
                canvas.create_line(x1, y + 11, x2, y + 11, fill="white", width=2, dash=(3, 2))
        if self.media[0] and self.media[1]:
            common_start = max(Fraction(0), self.offset)
            common_end = min(self.duration(0), self.offset + self.duration(1))
            if common_end > common_start:
                canvas.create_rectangle(self.timeline_x(common_start), 5, self.timeline_x(common_end), 92, outline="#e39b35", width=2)
        cx = self.timeline_x(self.cursor)
        canvas.create_line(cx, 2, cx, 96, fill="#d84343", width=2)
        canvas.create_text(16, 96, text=f"{float(start):.2f}s", anchor="sw", fill="#555")
        canvas.create_text(width + 15, 96, text=f"{float(end):.2f}s", anchor="se", fill="#555")

    def timeline_click(self, event: tk.Event) -> None:
        if not self.media[1]:
            self.cursor = self.x_timeline(event.x)
            self.refresh()
            return
        bx1, bx2 = self.timeline_x(self.offset), self.timeline_x(self.offset + self.duration(1))
        if min(bx1, bx2) - 8 <= event.x <= max(bx1, bx2) + 8:
            self.dragging = True
            self.drag_origin = self.x_timeline(event.x) - self.offset
            self.timeline.focus_set()
            self.timeline_drag(event)
        else:
            self.cursor = self.x_timeline(event.x)
            self.refresh()

    def timeline_drag(self, event: tk.Event) -> None:
        if self.dragging and self.media[1]:
            self.offset = self.x_timeline(event.x) - self.drag_origin
            self.refresh()

    def nudge(self, amount: Fraction) -> None:
        self.offset += amount
        self.refresh()

    def scrubbed(self, value: str) -> None:
        if self.playing:
            return
        self.cursor = Fraction(str(float(value)))
        self.refresh(preview=True)

    def refresh(self, preview: bool = True) -> None:
        self.draw_timeline()
        start, end = self.bounds()
        self.scrubber.configure(from_=float(start), to=float(end))
        self.cursor_var.set(float(self.cursor))
        self.clock_label.configure(text=_clock(self.cursor))
        if preview:
            for i, info in enumerate(self.media):
                if not info:
                    continue
                source_time = self.cursor if i == 0 else self.cursor - self.offset
                if 0 <= source_time < info.duration:
                    try:
                        encoded = base64.b64encode(frame_png(info, source_time)).decode("ascii")
                        photo = tk.PhotoImage(data=encoded)
                        self.photos[i] = photo
                        self.preview_labels[i].configure(image=photo, text="")
                    except Exception as exc:
                        self.preview_labels[i].configure(image="", text=str(exc))
                else:
                    self.photos[i] = None
                    self.preview_labels[i].configure(image="", text="Outside this clip")
        self.update_result()

    def update_result(self) -> None:
        a, b = self.media
        self.offset_label.configure(text=f"Offset to apply to second video: {'+' if self.offset >= 0 else '−'}{abs(float(self.offset)):.6f} s  ({_clock(self.offset)})")
        if not a or not b:
            self.result_label.configure(text="Choose both videos to calculate shared trims.")
            return
        alignment = calculate_alignment(a.duration, b.duration, self.offset)
        if not alignment.overlap_exists:
            self.result_label.configure(text="No shared interval at this offset. Drag video B so it overlaps video A.")
            return
        trims = alignment.trims(a.duration, b.duration)
        self.result_label.configure(text=(
            f"Shared duration: {_seconds(alignment.duration)} s ({_clock(alignment.duration)})\n"
            f"A trims — start {_seconds(trims['a_start'])} s, end {_seconds(trims['a_end'])} s\n"
            f"B trims — start {_seconds(trims['b_start'])} s, end {_seconds(trims['b_end'])} s"
        ))

    def copy_offset(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(_seconds(self.offset))
        self.status.configure(text=f"Copied signed offset {_seconds(self.offset)} seconds.")

    def toggle_play(self) -> None:
        if self.playing:
            self.stop_playback()
        else:
            self.start_playback()

    def start_playback(self) -> None:
        if not all(self.media):
            messagebox.showinfo("Choose two videos", "Select both videos before playback.", parent=self.root)
            return
        try:
            _, _, ffplay = require_tools()
        except RuntimeError as exc:
            messagebox.showerror("FFmpeg required", str(exc), parent=self.root)
            return
        alignment: Alignment = calculate_alignment(self.duration(0), self.duration(1), self.offset)
        if not alignment.overlap_exists:
            messagebox.showwarning("No shared interval", "Adjust video B until the clips overlap before playing them together.", parent=self.root)
            return
        self.stop_playback()
        self.play_end = alignment.common_end
        self.cursor = min(max(self.cursor, alignment.common_start), alignment.common_end)
        for index, info in enumerate(self.media):
            if not info or not info.has_audio:
                continue
            source_time = self.cursor if index == 0 else self.cursor - self.offset
            remaining = self.play_end - self.cursor
            cmd = [ffplay, "-nodisp", "-autoexit", "-loglevel", "error", "-ss", f"{float(source_time):.6f}", "-t", f"{float(remaining):.6f}", str(info.path)]
            self.audio_processes.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self.playing = True
        self.play_start_cursor = self.cursor
        self.play_started = time.monotonic()
        self.play_button.configure(text="Pause")
        self.status.configure(text="Playing both clips on the shared timeline. The dashed lines indicate audio tracks.")
        self._tick()

    def _tick(self) -> None:
        if not self.playing:
            return
        self.cursor = min(self.play_end, self.play_start_cursor + Fraction(str(time.monotonic() - self.play_started)))
        self.refresh(preview=True)
        if self.cursor >= self.play_end or (self.audio_processes and not any(p.poll() is None for p in self.audio_processes)):
            self.stop_playback()
        else:
            self.root.after(500, self._tick)

    def stop_playback(self) -> None:
        self.playing = False
        for process in self.audio_processes:
            if process.poll() is None:
                process.terminate()
        self.audio_processes.clear()
        if hasattr(self, "play_button"):
            self.play_button.configure(text="Play both audio tracks")

    def close(self) -> None:
        self.stop_playback()
        self.root.destroy()


def main() -> int:
    try:
        require_tools()
    except RuntimeError as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("FFmpeg required", str(exc))
        root.destroy()
        return 1
    root = tk.Tk()
    VideoStitcherApp(root)
    root.mainloop()
    return 0
