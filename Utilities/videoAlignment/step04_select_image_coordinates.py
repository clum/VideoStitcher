"""Tkinter GUI for recording corresponding points in two images."""

from __future__ import annotations

import base64
import json
import tempfile
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import Canvas, Listbox, StringVar, Tk, filedialog, messagebox, ttk

import cv2
import numpy as np

from .workflow_paths import results_directory, scenario_prefix


IMAGE_FILE_TYPES = (
    ("Image files", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
    ("All files", "*.*"),
)


def export_coordinates_to_txt(json_path: Path, txt_path: Path | None = None) -> Path:
    """Write each image path followed by its ordered ``[x, y],`` point lines."""
    json_path = Path(json_path)
    txt_path = Path(txt_path) if txt_path is not None else json_path.with_suffix(".txt")
    if txt_path.resolve() == json_path.resolve():
        raise ValueError("The text output path must not overwrite the coordinate JSON file.")
    try:
        record = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read coordinate JSON file {json_path}: {exc}") from exc

    if not isinstance(record, dict):
        raise ValueError("The JSON root must be an object containing Left and Right point lists.")

    groups: list[tuple[str, list[tuple[int, int]]]] = []
    for side_name in ("Left", "Right"):
        side = record.get(side_name)
        if not isinstance(side, dict):
            raise ValueError(f"The JSON record must contain a {side_name} image object.")
        image_path = side.get("ImagePath")
        if not isinstance(image_path, str) or not image_path.strip():
            raise ValueError(f"The JSON record must contain a valid {side_name}.ImagePath.")
        if not isinstance(side.get("Points"), list):
            raise ValueError(f"The JSON record must contain a {side_name}.Points list.")
        coordinates: list[tuple[int, int]] = []
        for point_index, point in enumerate(side["Points"], start=1):
            if not isinstance(point, dict):
                raise ValueError(f"{side_name}.Points[{point_index}] must be an object.")
            x, y = point.get("x"), point.get("y")
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
                raise ValueError(f"{side_name}.Points[{point_index}] must have integer x and y values.")
            coordinates.append((x, y))
        groups.append((image_path, coordinates))

    contents = "\n\n".join(
        image_path + "\n" + "".join(f"[{x}, {y}],\n" for x, y in coordinates)
        for image_path, coordinates in groups
    )
    try:
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(contents, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ValueError(f"Could not write coordinate text file {txt_path}: {exc}") from exc
    return txt_path


class ImageSide:
    """Widgets and coordinate state for one independently selected image."""

    def __init__(self, app: "ImageCoordinateApp", name: str, title: str) -> None:
        self.app = app
        self.name = name
        self.image_path: Path | None = None
        self.image = None
        self.width = 0
        self.height = 0
        self.points: list[tuple[int, int]] = []
        self.selected_index: int | None = None
        self.move_index: int | None = None
        self.display_box: tuple[float, float, float, float] | None = None
        self.photo: tk.PhotoImage | None = None

        self.frame = ttk.LabelFrame(app.panels, text=title, padding=8)
        self.frame.grid(row=0, column=0 if name == "left" else 1, sticky="nsew", padx=6)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(2, weight=1)
        self.path_var = StringVar(value="No image selected")
        ttk.Button(self.frame, text="Browse…", command=self.browse).grid(row=0, column=0, sticky="w")
        ttk.Label(self.frame, textvariable=self.path_var, wraplength=460).grid(
            row=1, column=0, sticky="ew", pady=(4, 6)
        )

        self.canvas = Canvas(self.frame, background="#171717", highlightthickness=0)
        self.canvas.grid(row=2, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self.render)
        self.canvas.bind("<Button-1>", self.canvas_click)

        ttk.Label(self.frame, text="Coordinates (x, y pixels from top-left):").grid(
            row=3, column=0, sticky="w", pady=(8, 2)
        )
        self.point_list = Listbox(self.frame, height=6, exportselection=False)
        self.point_list.grid(row=4, column=0, sticky="ew")
        self.point_list.bind("<<ListboxSelect>>", self.select_point)

        actions = ttk.Frame(self.frame)
        actions.grid(row=5, column=0, sticky="w", pady=(5, 0))
        self.remove_button = ttk.Button(actions, text="Remove selected", command=self.remove_point)
        self.remove_button.pack(side="left")
        self.move_button = ttk.Button(actions, text="Move selected", command=self.begin_move)
        self.move_button.pack(side="left", padx=(6, 0))

    def browse(self) -> None:
        path_text = filedialog.askopenfilename(
            title=f"Select {self.name} image", filetypes=IMAGE_FILE_TYPES
        )
        if not path_text:
            return
        candidate = Path(path_text)
        try:
            encoded = candidate.read_bytes()
            image = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None or image.ndim < 2:
                raise ValueError("OpenCV could not decode this image file.")
            height, width = image.shape[:2]
            if width <= 0 or height <= 0:
                raise ValueError("The decoded image has invalid dimensions.")
        except (OSError, ValueError, cv2.error) as exc:
            messagebox.showerror(
                f"Invalid {self.name} image",
                f"Could not load the {self.name} image:\n{candidate}\n\n{exc}",
                parent=self.app.root,
            )
            return

        self.image_path = candidate.resolve()
        self.image = image
        self.width, self.height = width, height
        self.points.clear()
        self.selected_index = None
        self.move_index = None
        self.path_var.set(str(self.image_path))
        self.refresh_points()
        self.render()
        self.app.set_status(f"Loaded {self.name} image: {width} × {height} pixels.")

    def refresh_points(self) -> None:
        self.point_list.delete(0, "end")
        for index, (x, y) in enumerate(self.points, start=1):
            self.point_list.insert("end", f"{index}: ({x}, {y})")
        if self.selected_index is not None and self.selected_index < len(self.points):
            self.point_list.selection_set(self.selected_index)
            self.point_list.activate(self.selected_index)
        self.render()

    def select_point(self, _event=None) -> None:
        selection = self.point_list.curselection()
        self.selected_index = selection[0] if selection else None
        self.render()

    def canvas_click(self, event) -> None:
        if self.image is None or self.display_box is None:
            return
        left, top, shown_width, shown_height = self.display_box
        if not (left <= event.x < left + shown_width and top <= event.y < top + shown_height):
            return
        x = min(self.width - 1, max(0, int((event.x - left) * self.width / shown_width)))
        y = min(self.height - 1, max(0, int((event.y - top) * self.height / shown_height)))

        if self.move_index is not None:
            if self.move_index < len(self.points):
                self.points[self.move_index] = (x, y)
                self.selected_index = self.move_index
            self.move_index = None
            self.move_button.configure(text="Move selected")
            self.app.set_status(f"Moved selected {self.name} point to ({x}, {y}).")
        else:
            self.points.append((x, y))
            self.selected_index = len(self.points) - 1
            self.app.set_status(f"Added {self.name} point ({x}, {y}).")
        self.refresh_points()

    def remove_point(self) -> None:
        if self.selected_index is None or self.selected_index >= len(self.points):
            self.app.set_status(f"Select a {self.name} point to remove.")
            return
        removed = self.points.pop(self.selected_index)
        self.selected_index = min(self.selected_index, len(self.points) - 1) if self.points else None
        self.move_index = None
        self.move_button.configure(text="Move selected")
        self.refresh_points()
        self.app.set_status(f"Removed {self.name} point {removed}.")

    def begin_move(self) -> None:
        if self.selected_index is None or self.selected_index >= len(self.points):
            self.app.set_status(f"Select a {self.name} point to move.")
            return
        self.move_index = self.selected_index
        self.move_button.configure(text="Click new location…")
        self.app.set_status(f"Click inside the {self.name} image to move the selected point.")

    def render(self, _event=None) -> None:
        self.canvas.delete("all")
        self.display_box = None
        self.photo = None
        if self.image is None:
            self.canvas.create_text(
                max(1, self.canvas.winfo_width() // 2),
                max(1, self.canvas.winfo_height() // 2),
                text=f"Browse for the {self.name} image",
                fill="white",
            )
            return

        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        scale = min(canvas_width / self.width, canvas_height / self.height)
        shown_width = max(1, int(round(self.width * scale)))
        shown_height = max(1, int(round(self.height * scale)))
        left = (canvas_width - shown_width) / 2
        top = (canvas_height - shown_height) / 2
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_NEAREST
        resized = cv2.resize(self.image, (shown_width, shown_height), interpolation=interpolation)
        # OpenCV's encoder expects BGR input; converting to RGB here swaps the
        # red and blue channels in the PNG passed to Tkinter.
        ok, png = cv2.imencode(".png", resized)
        if not ok:
            self.app.set_status(f"Could not render the {self.name} image.")
            return
        self.photo = tk.PhotoImage(
            master=self.canvas, data=base64.b64encode(png.tobytes()).decode("ascii")
        )
        self.canvas.create_image(left, top, image=self.photo, anchor="nw")
        self.display_box = (left, top, shown_width, shown_height)

        for index, (x, y) in enumerate(self.points):
            marker_x = left + (x + 0.5) * shown_width / self.width
            marker_y = top + (y + 0.5) * shown_height / self.height
            radius = 7 if index == self.selected_index else 5
            color = "#00ff66" if index == self.selected_index else "#ff3030"
            self.canvas.create_oval(
                marker_x - radius,
                marker_y - radius,
                marker_x + radius,
                marker_y + radius,
                outline="white",
                width=2,
                fill=color,
            )


class ImageCoordinateApp:
    def __init__(self, root: Tk, repository_root: Path) -> None:
        self.root = root
        self.repository_root = repository_root
        root.title("Video Stitcher — Select Image Coordinates")
        root.minsize(900, 650)

        self.panels = ttk.Frame(root, padding=10)
        self.panels.pack(fill="both", expand=True)
        self.panels.columnconfigure(0, weight=1)
        self.panels.columnconfigure(1, weight=1)
        self.panels.rowconfigure(0, weight=1)
        self.left = ImageSide(self, "left", "Left image")
        self.right = ImageSide(self, "right", "Right image")

        footer = ttk.Frame(root, padding=(10, 2, 10, 10))
        footer.pack(fill="x")
        self.status_var = StringVar(value="Browse for an image on each side, then click to add points.")
        ttk.Label(footer, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text="Export JSON to TXT…", command=self.export_coordinates).pack(
            side="right", padx=(0, 8)
        )
        ttk.Button(footer, text="Save coordinates…", command=self.save).pack(side="right")

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def export_coordinates(self) -> None:
        json_name = filedialog.askopenfilename(
            title="Select a coordinate JSON file",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            parent=self.root,
        )
        if not json_name:
            return
        json_path = Path(json_name)
        txt_name = filedialog.asksaveasfilename(
            title="Save coordinates as text",
            initialdir=str(json_path.parent),
            initialfile=f"{json_path.stem}.txt",
            defaultextension=".txt",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
            parent=self.root,
        )
        if not txt_name:
            return
        txt_path = Path(txt_name)
        try:
            exported_path = export_coordinates_to_txt(json_path, txt_path)
        except ValueError as exc:
            messagebox.showerror("Coordinate export failed", str(exc), parent=self.root)
            return
        self.set_status(f"Exported coordinates to: {exported_path}")
        messagebox.showinfo(
            "Coordinates exported", f"Coordinates saved to:\n{exported_path}", parent=self.root
        )

    def save(self) -> None:
        sides = (self.left, self.right)
        missing = [side.name for side in sides if side.image_path is None]
        empty = [side.name for side in sides if side.image_path is not None and not side.points]
        if missing or empty:
            details = []
            if missing:
                details.append("Select an image for: " + ", ".join(missing) + ".")
            if empty:
                details.append("Add at least one point to: " + ", ".join(empty) + ".")
            messagebox.showwarning("Incomplete coordinates", "\n".join(details), parent=self.root)
            return

        settings_path = self.repository_root / "settings.json"
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            output_dir = results_directory(settings, self.repository_root) / "Step04_ImageCoordinates"
            output_dir.mkdir(parents=True, exist_ok=True)
            scenario_value = settings.get("ScenarioName")
            if not isinstance(scenario_value, str) or not scenario_value.strip():
                raise ValueError("Set a non-empty ScenarioName in settings.json.")
            scenario = scenario_prefix(scenario_value)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            output_path = output_dir / f"{scenario}_Coordinates_{timestamp}.json"
            suffix = 1
            while output_path.exists():
                output_path = output_dir / f"{scenario}_Coordinates_{timestamp}_{suffix}.json"
                suffix += 1
            record = {
                "ScenarioName": settings.get("ScenarioName"),
                "CreatedAtLocal": datetime.now().astimezone().isoformat(timespec="seconds"),
                "Left": self._side_record(self.left),
                "Right": self._side_record(self.right),
            }
            self._write_json_atomically(output_path, record)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            messagebox.showerror(
                "Could not save coordinates",
                f"Could not save the coordinate record.\nDestination: {locals().get('output_path', 'could not be resolved')}\n\n{exc}",
                parent=self.root,
            )
            return

        self.set_status(f"Saved coordinate record: {output_path}")
        messagebox.showinfo("Coordinates saved", f"Saved coordinate record to:\n{output_path}", parent=self.root)

    @staticmethod
    def _side_record(side: ImageSide) -> dict:
        return {
            "ImagePath": str(side.image_path),
            "Width_px": side.width,
            "Height_px": side.height,
            "Points": [{"x": x, "y": y} for x, y in side.points],
        }

    @staticmethod
    def _write_json_atomically(output_path: Path, record: dict) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(record, temporary, indent=2)
                temporary.write("\n")
            temporary_path.replace(output_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def main(repository_root: Path) -> int:
    root = Tk()
    ImageCoordinateApp(root, repository_root.resolve())
    try:
        root.state("zoomed")
    except tk.TclError:
        # Some window managers use the fullscreen attribute to maximize while
        # keeping title-bar and window controls available.
        root.attributes("-zoomed", True)
    root.mainloop()
    return 0
