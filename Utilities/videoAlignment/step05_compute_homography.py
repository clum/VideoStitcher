"""Estimate and save the homography from a Step 04 coordinate record."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from .stitching_common import (
    StitchingError,
    load_coordinate_data,
    load_settings,
    resolve_coordinate_record,
)
from .workflow_paths import results_directory, scenario_prefix


RANSAC_REPROJECTION_THRESHOLD_PX = 5.0


def _estimate_homography(left_points: np.ndarray, right_points: np.ndarray) -> tuple[np.ndarray, int]:
    matrix, inlier_mask = cv2.findHomography(
        right_points,
        left_points,
        method=cv2.RANSAC,
        ransacReprojThreshold=RANSAC_REPROJECTION_THRESHOLD_PX,
    )
    if matrix is None or matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise StitchingError("Could not estimate a finite homography. Check the corresponding point pairs.")
    inliers = int(inlier_mask.sum()) if inlier_mask is not None else 0
    return matrix, inliers


def _save_homography(
    settings: dict,
    repository_root: Path,
    coordinate_path: Path,
    matrix: np.ndarray,
    inlier_count: int,
    point_count: int,
) -> Path:
    scenario = scenario_prefix(settings["ScenarioName"])
    output_dir = results_directory(settings, repository_root) / "Step05_Homography"
    output_path = output_dir / f"{scenario}_Homography.json"
    record = {
        "ScenarioName": settings["ScenarioName"],
        "CreatedAtLocal": datetime.now().astimezone().isoformat(timespec="seconds"),
        "CoordinateRecordPath": str(coordinate_path.resolve()),
        "HomographyMatrix": matrix.tolist(),
        "RansacInlierCount": inlier_count,
        "PointPairCount": point_count,
        "RansacReprojectionThreshold_px": RANSAC_REPROJECTION_THRESHOLD_PX,
    }
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=output_dir,
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
    except OSError as exc:
        raise StitchingError(f"Could not save homography to {output_path}: {exc}") from exc
    return output_path


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute the Step 05 homography from Step 04 points.")
    parser.add_argument(
        "--settings",
        type=Path,
        help="Settings JSON to use (relative paths resolve from the repository root; default: settings.json).",
    )
    parser.add_argument(
        "--coordinates",
        type=Path,
        help="Step 04 coordinate JSON (default: newest record for the configured scenario).",
    )
    return parser.parse_args(argv)


def run(repository_root: Path, argv: Sequence[str] | None = None) -> Path:
    args = _parse_args(argv)
    root = repository_root.resolve()
    settings = load_settings(root, args.settings)
    coordinate_path = resolve_coordinate_record(settings, root, args.coordinates)
    coordinates = load_coordinate_data(coordinate_path, settings, root)
    matrix, inliers = _estimate_homography(coordinates.left_points, coordinates.right_points)
    output_path = _save_homography(
        settings,
        root,
        coordinates.path,
        matrix,
        inliers,
        len(coordinates.left_points),
    )
    print("Homography matrix (right image to left image):\n", matrix)
    print(f"RANSAC inliers: {inliers} of {len(coordinates.left_points)}")
    print(f"Coordinate record: {coordinates.path}")
    print(f"Homography saved to: {output_path.resolve()}")
    return output_path


def main(repository_root: Path | None = None, argv: Sequence[str] | None = None) -> int:
    root = repository_root or Path(__file__).resolve().parents[2]
    try:
        run(root, argv)
    except (StitchingError, OSError, ValueError, cv2.error) as exc:
        print(f"Step 05 failed: {exc}", file=sys.stderr)
        return 1
    return 0
