"""Shared settings and Step 04 coordinate-record handling for Steps 05 and 06."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .workflow_paths import results_directory, scenario_prefix


class StitchingError(RuntimeError):
    """An actionable image-stitching workflow error."""


@dataclass(frozen=True)
class CoordinateData:
    path: Path
    payload: dict[str, Any]
    left_path: Path
    right_path: Path
    left_width: int
    left_height: int
    right_width: int
    right_height: int
    left_points: np.ndarray
    right_points: np.ndarray


def load_settings(repository_root: Path, settings_path: Path | None = None) -> dict[str, Any]:
    settings_path = settings_path or (repository_root / "settings.json")
    settings_path = settings_path.expanduser()
    if not settings_path.is_absolute():
        settings_path = repository_root / settings_path
    settings_path = settings_path.resolve()
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise StitchingError(f"Settings file not found: {settings_path}") from exc
    except json.JSONDecodeError as exc:
        raise StitchingError(f"Settings file {settings_path} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise StitchingError(f"Could not read settings file {settings_path}: {exc}") from exc
    if not isinstance(settings, dict):
        raise StitchingError("settings.json must contain a JSON object.")
    scenario = settings.get("ScenarioName")
    if not isinstance(scenario, str) or not scenario.strip():
        raise StitchingError("Set a non-empty ScenarioName in settings.json.")
    try:
        scenario_prefix(scenario)
        results_directory(settings, repository_root)
    except ValueError as exc:
        raise StitchingError(str(exc)) from exc
    return settings


def resolve_coordinate_record(
    settings: dict[str, Any], repository_root: Path, requested_path: Path | None
) -> Path:
    if requested_path is not None:
        path = requested_path.expanduser()
        if not path.is_absolute():
            path = repository_root / path
        path = path.resolve()
        if not path.is_file():
            raise StitchingError(f"Coordinate record was not found: {path}")
        return path

    output = results_directory(settings, repository_root) / "Step04_ImageCoordinates"
    if not output.is_dir():
        raise StitchingError(
            f"Step 04 coordinate folder does not exist: {output}. "
            "Run Step04_SelectImageCoordinates.py first."
        )
    prefix = scenario_prefix(settings["ScenarioName"])
    candidates = sorted(output.glob(f"{prefix}_Coordinates_*.json"), key=lambda path: path.name)
    if not candidates:
        raise StitchingError(
            f"No coordinate record for scenario {settings['ScenarioName']!r} was found in {output}. "
            "Run Step04_SelectImageCoordinates.py first or pass --coordinates PATH."
        )
    return candidates[-1]


def _image_path(side_record: dict[str, Any], side: str, repository_root: Path) -> Path:
    value = side_record.get("ImagePath")
    if not isinstance(value, str) or not value.strip():
        raise StitchingError(f"Coordinate record must contain a valid {side}.ImagePath.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = repository_root / path
    return path.resolve()


def _dimension(side_record: dict[str, Any], key: str, side: str) -> int:
    value = side_record.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StitchingError(f"{side}.{key} must be a positive integer.")
    return value


def _points(side_record: dict[str, Any], side: str, width: int, height: int) -> np.ndarray:
    raw_points = side_record.get("Points")
    if not isinstance(raw_points, list):
        raise StitchingError(f"Coordinate record must contain a {side}.Points list.")
    points: list[tuple[int, int]] = []
    for index, point in enumerate(raw_points, start=1):
        if not isinstance(point, dict):
            raise StitchingError(f"{side}.Points[{index}] must be an object with integer x and y values.")
        x, y = point.get("x"), point.get("y")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (x, y)):
            raise StitchingError(f"{side}.Points[{index}] must have integer x and y values.")
        if not (0 <= x < width and 0 <= y < height):
            raise StitchingError(
                f"{side}.Points[{index}] ({x}, {y}) is outside the image bounds {width}x{height}."
            )
        points.append((x, y))
    return np.asarray(points, dtype=np.float32)


def load_coordinate_data(
    path: Path, settings: dict[str, Any], repository_root: Path
) -> CoordinateData:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise StitchingError(f"Coordinate record is not valid JSON ({path}): {exc}") from exc
    except OSError as exc:
        raise StitchingError(f"Could not read coordinate record {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StitchingError("Coordinate record must contain a JSON object.")
    scenario = settings["ScenarioName"]
    if payload.get("ScenarioName") != scenario:
        raise StitchingError(
            f"Coordinate record scenario {payload.get('ScenarioName')!r} does not match "
            f"settings.json ScenarioName {scenario!r}."
        )
    sides: dict[str, tuple[Path, int, int, np.ndarray]] = {}
    for side in ("Left", "Right"):
        value = payload.get(side)
        if not isinstance(value, dict):
            raise StitchingError(f"Coordinate record must contain a {side} image object.")
        width = _dimension(value, "Width_px", side)
        height = _dimension(value, "Height_px", side)
        sides[side] = (
            _image_path(value, side, repository_root),
            width,
            height,
            _points(value, side, width, height),
        )
    left_path, left_width, left_height, left_points = sides["Left"]
    right_path, right_width, right_height, right_points = sides["Right"]
    if len(left_points) != len(right_points):
        raise StitchingError(
            f"Left and Right point lists must have the same length; found "
            f"{len(left_points)} and {len(right_points)}."
        )
    if len(left_points) < 4:
        raise StitchingError(f"At least four matching point pairs are required; found {len(left_points)}.")
    return CoordinateData(
        path.resolve(), payload, left_path, right_path,
        left_width, left_height, right_width, right_height,
        left_points, right_points,
    )


def load_coordinate_images(data: CoordinateData) -> tuple[np.ndarray, np.ndarray]:
    images: list[np.ndarray] = []
    for side, path, expected_width, expected_height in (
        ("Left", data.left_path, data.left_width, data.left_height),
        ("Right", data.right_path, data.right_width, data.right_height),
    ):
        try:
            encoded = path.read_bytes()
            image = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        except (OSError, cv2.error) as exc:
            raise StitchingError(f"Could not read {side} image {path}: {exc}") from exc
        if image is None or image.ndim != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
            raise StitchingError(f"Could not decode {side} image or determine its dimensions: {path}")
        height, width = image.shape[:2]
        if (width, height) != (expected_width, expected_height):
            raise StitchingError(
                f"{side} image dimensions do not match the coordinate record for {path}: "
                f"recorded {expected_width}x{expected_height}, found {width}x{height}."
            )
        images.append(image)
    return images[0], images[1]
