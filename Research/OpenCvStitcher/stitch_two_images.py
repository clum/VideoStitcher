"""Align two images from manually matched landmarks using a homography."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import cv2
import numpy as np


HERE = Path(__file__).resolve().parent
DEFAULT_IMAGES = (
    HERE / "26_10_31_Mukilteo_CameraA_t050s.png",
    HERE / "26_10_31_Mukilteo_CameraB_t050s.png",
)

# Corresponding landmarks, in the same order in both arrays.
# Replace these example pixel coordinates when using different images.
# points_right are mapped into the coordinate system of points_left.
POINTS_LEFT = np.float32(
    [

        [1744, 578],
        [1717, 632],
        [1801, 545],
        [1804, 580],
        [1830, 640],
        [1725, 711],
        [1727, 733],
        [1868, 728],
        [1755, 1018],
        [1902, 395],
        [1690, 184],
    ]
)
POINTS_RIGHT = np.float32(
    [
        [53, 554],
        [26, 611],
        [112, 521],
                [116, 556],
        [141, 616],
        [37, 696],
        [45, 717],
        [181, 697],
        [125, 1001],
        [244, 381],
        [38, 141],
    ]
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Align two images from manually corresponding landmarks."
    )
    parser.add_argument(
        "images",
        nargs="*",
        type=Path,
        help="Left and right image paths (defaults to the sample Camera A and B frames).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=HERE,
        help=f"Directory for warped_right.jpg and alignment_preview.jpg (default: {HERE}).",
    )
    args = parser.parse_args()
    if args.images and len(args.images) != 2:
        parser.error("provide exactly two image paths, or none to use the sample frames")
    if not args.images:
        args.images = list(DEFAULT_IMAGES)
    return args


def validate_points(left: np.ndarray, right: np.ndarray, points_left: np.ndarray, points_right: np.ndarray) -> None:
    if points_left.ndim != 2 or points_left.shape[1:] != (2,):
        raise ValueError("POINTS_LEFT must be an array of (x, y) coordinate pairs.")
    if points_right.ndim != 2 or points_right.shape[1:] != (2,):
        raise ValueError("POINTS_RIGHT must be an array of (x, y) coordinate pairs.")
    if len(points_left) != len(points_right):
        raise ValueError("POINTS_LEFT and POINTS_RIGHT must contain the same number of points.")
    if len(points_left) < 4:
        raise ValueError("At least four corresponding point pairs are required.")

    for label, points, image in (
        ("POINTS_LEFT", points_left, left),
        ("POINTS_RIGHT", points_right, right),
    ):
        height, width = image.shape[:2]
        if np.any(points[:, 0] < 0) or np.any(points[:, 0] >= width):
            raise ValueError(f"{label} contains an x coordinate outside the image width ({width}).")
        if np.any(points[:, 1] < 0) or np.any(points[:, 1] >= height):
            raise ValueError(f"{label} contains a y coordinate outside the image height ({height}).")


def main() -> int:
    args = parse_args()
    left_path, right_path = args.images
    left = cv2.imread(str(left_path), cv2.IMREAD_COLOR)
    right = cv2.imread(str(right_path), cv2.IMREAD_COLOR)
    if left is None or right is None:
        missing = [str(path) for path, image in ((left_path, left), (right_path, right)) if image is None]
        print("Could not read image file(s): " + ", ".join(missing), file=sys.stderr)
        return 2

    try:
        validate_points(left, right, POINTS_LEFT, POINTS_RIGHT)
    except ValueError as exc:
        print(f"Invalid landmark coordinates: {exc}", file=sys.stderr)
        return 2

    homography, inlier_mask = cv2.findHomography(
        POINTS_RIGHT,
        POINTS_LEFT,
        method=cv2.RANSAC,
        ransacReprojThreshold=5.0,
    )
    if homography is None:
        print("Could not estimate a transform. Check the corresponding point pairs.", file=sys.stderr)
        return 1

    inlier_count = int(inlier_mask.sum()) if inlier_mask is not None else 0
    print("Homography matrix:\n", homography)
    print(f"Inliers: {inlier_count} of {len(POINTS_LEFT)}")

    height, width = left.shape[:2]
    warped_right = cv2.warpPerspective(right, homography, (width, height))
    preview = cv2.addWeighted(left, 0.5, warped_right, 0.5, 0)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    warped_path = args.output_dir / "warped_right.jpg"
    preview_path = args.output_dir / "alignment_preview.jpg"
    if not cv2.imwrite(str(warped_path), warped_right):
        print(f"Could not write warped image: {warped_path}", file=sys.stderr)
        return 2
    if not cv2.imwrite(str(preview_path), preview):
        print(f"Could not write alignment preview: {preview_path}", file=sys.stderr)
        return 2

    print(f"Warped right image saved to: {warped_path.resolve()}")
    print(f"Alignment preview saved to: {preview_path.resolve()}")
    cv2.imshow("Left image", left)
    cv2.imshow("Right warped onto left", warped_right)
    cv2.imshow("Alignment preview", preview)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
