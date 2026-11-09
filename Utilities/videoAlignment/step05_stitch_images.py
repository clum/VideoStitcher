"""Stitch the image pair and corresponding points saved by Step 04."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

from .workflow_paths import results_directory, scenario_prefix


class Step05Error(RuntimeError):
    """An actionable Step 05 input, transform, or output error."""


@dataclass(frozen=True)
class CoordinateImage:
    path: Path
    image: np.ndarray
    points: np.ndarray


def _load_settings(repository_root: Path) -> dict[str, Any]:
    settings_path = repository_root / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Step05Error(f"Settings file not found: {settings_path}") from exc
    except json.JSONDecodeError as exc:
        raise Step05Error(f"Settings file is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise Step05Error(f"Could not read settings file {settings_path}: {exc}") from exc
    if not isinstance(settings, dict):
        raise Step05Error("settings.json must contain a JSON object.")
    scenario = settings.get("ScenarioName")
    if not isinstance(scenario, str) or not scenario.strip():
        raise Step05Error("Set a non-empty ScenarioName in settings.json.")
    try:
        scenario_prefix(scenario)
        results_directory(settings, repository_root)
    except ValueError as exc:
        raise Step05Error(str(exc)) from exc
    return settings


def _coordinate_record_path(
    settings: dict[str, Any], repository_root: Path, requested_path: Path | None
) -> Path:
    if requested_path is not None:
        path = requested_path.expanduser()
        if not path.is_absolute():
            path = repository_root / path
        path = path.resolve()
        if not path.is_file():
            raise Step05Error(f"Coordinate record was not found: {path}")
        return path

    scenario = settings["ScenarioName"]
    try:
        folder = results_directory(settings, repository_root) / "Step04_ImageCoordinates"
    except ValueError as exc:
        raise Step05Error(str(exc)) from exc
    if not folder.is_dir():
        raise Step05Error(
            f"Step 04 coordinate folder does not exist: {folder}. Run Step04_SelectImageCoordinates.py first."
        )
    prefix = scenario_prefix(scenario)
    records = sorted(folder.glob(f"{prefix}_Coordinates_*.json"), key=lambda path: path.name)
    if not records:
        raise Step05Error(
            f"No coordinate record for scenario {scenario!r} was found in {folder}. "
            "Run Step04_SelectImageCoordinates.py first or pass --coordinates PATH."
        )
    return records[-1]


def _read_image(path: Path, side: str) -> np.ndarray:
    try:
        encoded = path.read_bytes()
        image = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    except (OSError, cv2.error) as exc:
        raise Step05Error(f"Could not read {side} image {path}: {exc}") from exc
    if image is None or image.ndim != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise Step05Error(f"Could not decode {side} image or determine its dimensions: {path}")
    return image


def _load_side(
    record: dict[str, Any], side: str, repository_root: Path
) -> CoordinateImage:
    value = record.get(side)
    if not isinstance(value, dict):
        raise Step05Error(f"Coordinate record must contain a {side} image object.")
    path_text = value.get("ImagePath")
    if not isinstance(path_text, str) or not path_text.strip():
        raise Step05Error(f"Coordinate record must contain a valid {side}.ImagePath.")
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = repository_root / path
    path = path.resolve()
    image = _read_image(path, side)

    height, width = image.shape[:2]
    recorded_width, recorded_height = value.get("Width_px"), value.get("Height_px")
    if (
        isinstance(recorded_width, bool)
        or isinstance(recorded_height, bool)
        or not isinstance(recorded_width, int)
        or not isinstance(recorded_height, int)
        or (recorded_width, recorded_height) != (width, height)
    ):
        raise Step05Error(
            f"{side} image dimensions do not match the coordinate record for {path}: "
            f"recorded {recorded_width}x{recorded_height}, found {width}x{height}."
        )

    raw_points = value.get("Points")
    if not isinstance(raw_points, list):
        raise Step05Error(f"Coordinate record must contain a {side}.Points list.")
    points: list[tuple[int, int]] = []
    for index, point in enumerate(raw_points, start=1):
        if not isinstance(point, dict):
            raise Step05Error(f"{side}.Points[{index}] must be an object with integer x and y values.")
        x, y = point.get("x"), point.get("y")
        if any(isinstance(coordinate, bool) or not isinstance(coordinate, int) for coordinate in (x, y)):
            raise Step05Error(f"{side}.Points[{index}] must have integer x and y values.")
        if not (0 <= x < width and 0 <= y < height):
            raise Step05Error(
                f"{side}.Points[{index}] ({x}, {y}) is outside the image bounds {width}x{height}."
            )
        points.append((x, y))
    return CoordinateImage(path, image, np.asarray(points, dtype=np.float32))


def _load_coordinates(
    path: Path, settings: dict[str, Any], repository_root: Path
) -> tuple[CoordinateImage, CoordinateImage]:
    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise Step05Error(f"Coordinate record is not valid JSON ({path}): {exc}") from exc
    except OSError as exc:
        raise Step05Error(f"Could not read coordinate record {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise Step05Error("Coordinate record must contain a JSON object.")
    scenario = settings["ScenarioName"]
    if record.get("ScenarioName") != scenario:
        raise Step05Error(
            f"Coordinate record scenario {record.get('ScenarioName')!r} does not match "
            f"settings.json ScenarioName {scenario!r}."
        )
    left = _load_side(record, "Left", repository_root)
    right = _load_side(record, "Right", repository_root)
    if len(left.points) != len(right.points):
        raise Step05Error(
            f"Left and Right point lists must have the same length; found "
            f"{len(left.points)} and {len(right.points)}."
        )
    if len(left.points) < 4:
        raise Step05Error(f"At least four matching point pairs are required; found {len(left.points)}.")
    return left, right


def _render(
    left: CoordinateImage, right: CoordinateImage, overlap_mode: str
) -> tuple[np.ndarray, np.ndarray, int, np.ndarray]:
    homography, inlier_mask = cv2.findHomography(
        right.points,
        left.points,
        method=cv2.RANSAC,
        ransacReprojThreshold=5.0,
    )
    if homography is None:
        raise Step05Error("Could not estimate a transform. Check the corresponding point pairs.")
    inlier_count = int(inlier_mask.sum()) if inlier_mask is not None else 0
    print("Homography matrix:\n", homography)
    print(f"Inliers: {inlier_count} of {len(left.points)}")

    left_height, left_width = left.image.shape[:2]
    right_height, right_width = right.image.shape[:2]
    right_corners = np.float32(
        [[0, 0], [right_width, 0], [right_width, right_height], [0, right_height]]
    ).reshape(-1, 1, 2)
    corner_denominators = np.c_[right_corners.reshape(-1, 2), np.ones(4)] @ homography[2, :]
    render_transform = homography
    if corner_denominators.min() <= 0 <= corner_denominators.max():
        affine, affine_inliers = cv2.estimateAffinePartial2D(
            right.points,
            left.points,
            method=cv2.RANSAC,
            ransacReprojThreshold=5.0,
        )
        affine_inlier_count = int(affine_inliers.sum()) if affine_inliers is not None else 0
        if affine is None or affine_inlier_count < 4:
            raise Step05Error(
                "The homography horizon crosses the right image, and too few landmark pairs "
                "support a stable full-frame similarity transform. Check the landmark pairs."
            )
        render_transform = np.vstack((affine, [0.0, 0.0, 1.0]))
        print(
            "The homography horizon crosses the right image; rendering with a robust "
            f"similarity transform instead ({affine_inlier_count} inliers)."
        )

    projected_corners = cv2.perspectiveTransform(right_corners, render_transform).reshape(-1, 2)
    if not np.isfinite(projected_corners).all():
        raise Step05Error("The transform projects image corners to non-finite coordinates.")
    min_x = min(0, int(np.floor(projected_corners[:, 0].min())))
    min_y = min(0, int(np.floor(projected_corners[:, 1].min())))
    max_x = max(left_width, int(np.ceil(projected_corners[:, 0].max())))
    max_y = max(left_height, int(np.ceil(projected_corners[:, 1].max())))
    canvas_width, canvas_height = max_x - min_x, max_y - min_y
    if canvas_width <= 0 or canvas_height <= 0 or canvas_width * canvas_height > 250_000_000:
        raise Step05Error(
            f"The estimated panorama canvas is unreasonable ({canvas_width}x{canvas_height}). "
            "Check the point pairs and transform."
        )

    right_min_x = int(np.floor(projected_corners[:, 0].min()))
    right_min_y = int(np.floor(projected_corners[:, 1].min()))
    right_max_x = int(np.ceil(projected_corners[:, 0].max()))
    right_max_y = int(np.ceil(projected_corners[:, 1].max()))
    right_width_out, right_height_out = right_max_x - right_min_x, right_max_y - right_min_y
    if right_width_out <= 0 or right_height_out <= 0:
        raise Step05Error("The transformed right image has invalid output dimensions.")

    panorama_transform = np.array(
        [[1.0, 0.0, -min_x], [0.0, 1.0, -min_y], [0.0, 0.0, 1.0]], dtype=np.float64
    ) @ render_transform
    right_only_transform = np.array(
        [[1.0, 0.0, -right_min_x], [0.0, 1.0, -right_min_y], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    ) @ render_transform
    warped_right = cv2.warpPerspective(
        right.image, right_only_transform, (right_width_out, right_height_out)
    )
    warped_right_on_panorama = cv2.warpPerspective(
        right.image, panorama_transform, (canvas_width, canvas_height)
    )
    left_on_canvas = np.zeros((canvas_height, canvas_width, 3), dtype=left.image.dtype)
    left_x, left_y = -min_x, -min_y
    left_on_canvas[left_y:left_y + left_height, left_x:left_x + left_width] = left.image

    right_mask = cv2.warpPerspective(
        np.full((right_height, right_width), 255, dtype=np.uint8),
        panorama_transform,
        (canvas_width, canvas_height),
        flags=cv2.INTER_NEAREST,
    ) > 0
    left_mask = np.zeros((canvas_height, canvas_width), dtype=bool)
    left_mask[left_y:left_y + left_height, left_x:left_x + left_width] = True
    preview = left_on_canvas.copy()
    preview[right_mask & ~left_mask] = warped_right_on_panorama[right_mask & ~left_mask]
    if overlap_mode == "blend":
        overlap = left_mask & right_mask
        preview[overlap] = cv2.addWeighted(
            left_on_canvas[overlap], 0.5, warped_right_on_panorama[overlap], 0.5, 0
        )
    print(f"Panorama canvas: {canvas_width}x{canvas_height}")
    return warped_right, preview, inlier_count, homography


def _publish_images(
    output_dir: Path, warped_right: np.ndarray, preview: np.ndarray
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    warped_path = output_dir / "warped_right.jpg"
    preview_path = output_dir / "alignment_preview.jpg"
    destinations = (warped_path, preview_path)
    backups: dict[Path, Path] = {}
    try:
        with tempfile.TemporaryDirectory(prefix=".step05_", dir=output_dir) as temporary:
            stage_dir = Path(temporary)
            staged_paths = (stage_dir / warped_path.name, stage_dir / preview_path.name)
            for image, staged_path in zip((warped_right, preview), staged_paths):
                if not cv2.imwrite(str(staged_path), image):
                    raise Step05Error(f"Could not write staged Step 05 image: {staged_path}")
            for destination in destinations:
                if destination.exists():
                    backup = stage_dir / f"backup_{destination.name}"
                    shutil.copy2(destination, backup)
                    backups[destination] = backup
            published: list[Path] = []
            try:
                for staged_path, destination in zip(staged_paths, destinations):
                    staged_path.replace(destination)
                    published.append(destination)
            except OSError as exc:
                rollback_errors: list[str] = []
                for destination in published:
                    try:
                        backup = backups.get(destination)
                        if backup is not None and backup.exists():
                            shutil.copy2(backup, destination)
                        else:
                            destination.unlink(missing_ok=True)
                    except OSError as rollback_error:
                        rollback_errors.append(f"Could not restore {destination}: {rollback_error}")
                if rollback_errors:
                    raise Step05Error(
                        f"Could not publish Step 05 outputs: {exc}; {'; '.join(rollback_errors)}"
                    ) from exc
                raise Step05Error(f"Could not publish Step 05 outputs in {output_dir}: {exc}") from exc
    except Step05Error:
        raise
    except OSError as exc:
        raise Step05Error(f"Could not prepare Step 05 output folder {output_dir}: {exc}") from exc
    return warped_path, preview_path


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stitch the image pair selected in Step 04.")
    parser.add_argument(
        "--coordinates",
        type=Path,
        help="Step 04 coordinate JSON (default: newest record for the configured scenario).",
    )
    parser.add_argument(
        "--overlap-mode",
        choices=("left", "blend"),
        default="left",
        help="Overlap behavior in alignment_preview.jpg (default: left).",
    )
    return parser.parse_args(argv)


def main(repository_root: Path | None = None, argv: Sequence[str] | None = None) -> int:
    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    args = _parse_args(argv)
    try:
        settings = _load_settings(root)
        coordinates_path = _coordinate_record_path(settings, root, args.coordinates)
        left, right = _load_coordinates(coordinates_path, settings, root)
        warped_right, preview, inliers, _homography = _render(left, right, args.overlap_mode)
        output_dir = results_directory(settings, root) / "Step05_StitchedImages"
        warped_path, preview_path = _publish_images(output_dir, warped_right, preview)
    except (Step05Error, OSError, ValueError, cv2.error) as exc:
        print(f"Step 05 failed: {exc}", file=sys.stderr)
        return 1

    print(f"Coordinate record: {coordinates_path}")
    print(f"Warped right image saved to: {warped_path.resolve()}")
    print(f"Alignment preview saved to: {preview_path.resolve()}")
    try:
        cv2.imshow("Left image", left.image)
        cv2.imshow("Full right image warped onto panorama canvas", warped_right)
        cv2.imshow("Alignment preview", preview)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    except cv2.error as exc:
        print(
            "Step 05 images were saved, but the OpenCV preview window could not be opened. "
            f"Use the VideoStitcher Conda environment with GUI support. Details: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0
