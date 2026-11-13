"""Apply the saved Step 05 homography and build stitched image outputs."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from .stitching_common import (
    StitchingError,
    load_coordinate_data,
    load_coordinate_images,
    load_settings,
)
from .workflow_paths import results_directory, scenario_prefix


def _load_homography(settings: dict, repository_root: Path) -> tuple[np.ndarray, Path, Path]:
    scenario = settings["ScenarioName"]
    artifact_path = (
        results_directory(settings, repository_root)
        / "Step05_Homography"
        / f"{scenario_prefix(scenario)}_Homography.json"
    )
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise StitchingError(
            f"Step 05 homography was not found at {artifact_path}. Run Step05_ComputeHomography.py first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise StitchingError(f"Step 05 homography file is not valid JSON ({artifact_path}): {exc}") from exc
    except OSError as exc:
        raise StitchingError(f"Could not read Step 05 homography {artifact_path}: {exc}") from exc
    if not isinstance(artifact, dict):
        raise StitchingError("Step 05 homography artifact must contain a JSON object.")
    if artifact.get("ScenarioName") != scenario:
        raise StitchingError(
            f"Step 05 homography scenario {artifact.get('ScenarioName')!r} does not match "
            f"settings.json ScenarioName {scenario!r}."
        )
    raw_matrix = artifact.get("HomographyMatrix")
    try:
        matrix = np.asarray(raw_matrix, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise StitchingError("HomographyMatrix must be a finite 3x3 numeric matrix.") from exc
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or np.linalg.matrix_rank(matrix) < 3:
        raise StitchingError("HomographyMatrix must be a finite, invertible 3x3 numeric matrix.")
    coordinate_path_text = artifact.get("CoordinateRecordPath")
    if not isinstance(coordinate_path_text, str) or not coordinate_path_text.strip():
        raise StitchingError("Step 05 homography artifact is missing CoordinateRecordPath.")
    coordinate_path = Path(coordinate_path_text).expanduser()
    if not coordinate_path.is_absolute():
        coordinate_path = repository_root / coordinate_path
    coordinate_path = coordinate_path.resolve()
    if not coordinate_path.is_file():
        raise StitchingError(f"Coordinate record referenced by Step 05 is missing: {coordinate_path}")
    return matrix, coordinate_path, artifact_path


def _render(
    left: np.ndarray,
    right: np.ndarray,
    left_points: np.ndarray,
    right_points: np.ndarray,
    homography: np.ndarray,
    overlap_mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    left_height, left_width = left.shape[:2]
    right_height, right_width = right.shape[:2]
    right_corners = np.float32(
        [[0, 0], [right_width, 0], [right_width, right_height], [0, right_height]]
    ).reshape(-1, 1, 2)
    corner_denominators = np.c_[right_corners.reshape(-1, 2), np.ones(4)] @ homography[2, :]
    render_transform = homography
    if corner_denominators.min() <= 0 <= corner_denominators.max():
        affine, affine_inliers = cv2.estimateAffinePartial2D(
            right_points,
            left_points,
            method=cv2.RANSAC,
            ransacReprojThreshold=5.0,
        )
        affine_inlier_count = int(affine_inliers.sum()) if affine_inliers is not None else 0
        if affine is None or affine_inlier_count < 4:
            raise StitchingError(
                "The homography horizon crosses the right image, and too few landmark pairs "
                "support a stable full-frame similarity transform. Check the landmark pairs."
            )
        render_transform = np.vstack((affine, [0.0, 0.0, 1.0]))
        print(
            "The saved homography horizon crosses the right image; rendering with a robust "
            f"similarity transform instead ({affine_inlier_count} inliers)."
        )

    projected_corners = cv2.perspectiveTransform(right_corners, render_transform).reshape(-1, 2)
    if not np.isfinite(projected_corners).all():
        raise StitchingError("The transform projects image corners to non-finite coordinates.")
    min_x = min(0, int(np.floor(projected_corners[:, 0].min())))
    min_y = min(0, int(np.floor(projected_corners[:, 1].min())))
    max_x = max(left_width, int(np.ceil(projected_corners[:, 0].max())))
    max_y = max(left_height, int(np.ceil(projected_corners[:, 1].max())))
    canvas_width, canvas_height = max_x - min_x, max_y - min_y
    if canvas_width <= 0 or canvas_height <= 0 or canvas_width * canvas_height > 250_000_000:
        raise StitchingError(
            f"The estimated panorama canvas is unreasonable ({canvas_width}x{canvas_height}). "
            "Check the point pairs and homography."
        )

    right_min_x = int(np.floor(projected_corners[:, 0].min()))
    right_min_y = int(np.floor(projected_corners[:, 1].min()))
    right_max_x = int(np.ceil(projected_corners[:, 0].max()))
    right_max_y = int(np.ceil(projected_corners[:, 1].max()))
    right_width_out, right_height_out = right_max_x - right_min_x, right_max_y - right_min_y
    if right_width_out <= 0 or right_height_out <= 0:
        raise StitchingError("The transformed right image has invalid output dimensions.")

    panorama_transform = np.array(
        [[1.0, 0.0, -min_x], [0.0, 1.0, -min_y], [0.0, 0.0, 1.0]], dtype=np.float64
    ) @ render_transform
    right_only_transform = np.array(
        [[1.0, 0.0, -right_min_x], [0.0, 1.0, -right_min_y], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    ) @ render_transform
    warped_right = cv2.warpPerspective(right, right_only_transform, (right_width_out, right_height_out))
    warped_right_on_panorama = cv2.warpPerspective(
        right, panorama_transform, (canvas_width, canvas_height)
    )
    left_on_canvas = np.zeros((canvas_height, canvas_width, 3), dtype=left.dtype)
    left_x, left_y = -min_x, -min_y
    left_on_canvas[left_y:left_y + left_height, left_x:left_x + left_width] = left
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
    return warped_right, preview


def _publish_images(
    output_dir: Path, warped_right: np.ndarray, preview: np.ndarray
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    warped_path = output_dir / "warped_right.jpg"
    preview_path = output_dir / "alignment_preview.jpg"
    destinations = (warped_path, preview_path)
    backups: dict[Path, Path] = {}
    try:
        with tempfile.TemporaryDirectory(prefix=".step06_", dir=output_dir) as temporary:
            stage_dir = Path(temporary)
            staged_paths = (stage_dir / warped_path.name, stage_dir / preview_path.name)
            for image, staged_path in zip((warped_right, preview), staged_paths):
                if not cv2.imwrite(str(staged_path), image):
                    raise StitchingError(f"Could not write staged Step 06 image: {staged_path}")
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
                    raise StitchingError(
                        f"Could not publish Step 06 outputs: {exc}; {'; '.join(rollback_errors)}"
                    ) from exc
                raise StitchingError(f"Could not publish Step 06 outputs in {output_dir}: {exc}") from exc
    except StitchingError:
        raise
    except OSError as exc:
        raise StitchingError(f"Could not prepare Step 06 output folder {output_dir}: {exc}") from exc
    return warped_path, preview_path


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply the homography computed by Step 05.")
    parser.add_argument(
        "--settings",
        type=Path,
        help="Settings JSON to use (relative paths resolve from the repository root; default: settings.json).",
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
        settings = load_settings(root, args.settings)
        homography, coordinates_path, artifact_path = _load_homography(settings, root)
        coordinates = load_coordinate_data(coordinates_path, settings, root)
        left, right = load_coordinate_images(coordinates)
        warped_right, preview = _render(
            left,
            right,
            coordinates.left_points,
            coordinates.right_points,
            homography,
            args.overlap_mode,
        )
        output_dir = results_directory(settings, root) / "Step06_StitchedImages"
        warped_path, preview_path = _publish_images(output_dir, warped_right, preview)
    except (StitchingError, OSError, ValueError, cv2.error) as exc:
        print(f"Step 06 failed: {exc}", file=sys.stderr)
        return 1

    print(f"Homography artifact: {artifact_path}")
    print(f"Warped right image saved to: {warped_path.resolve()}")
    print(f"Alignment preview saved to: {preview_path.resolve()}")
    try:
        cv2.imshow("Left image", left)
        cv2.imshow("Full right image warped onto panorama canvas", warped_right)
        cv2.imshow("Alignment preview", preview)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    except cv2.error as exc:
        print(
            "Step 06 images were saved, but the OpenCV preview window could not be opened. "
            f"Use the VideoStitcher Conda environment with GUI support. Details: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0
