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
    parser.add_argument(
        "--overlap-mode",
        choices=("left", "blend"),
        default="left",
        help="How to composite overlapping pixels in the preview (default: left).",
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

    left_height, left_width = left.shape[:2]
    right_height, right_width = right.shape[:2]

    # A homography can map a line inside the source frame to infinity. When
    # that projective horizon crosses the right image, the full-frame warp
    # folds into disconnected pieces. This often happens when landmarks only
    # cover a narrow overlap region. Use a robust similarity transform for
    # rendering in that case; retain the homography above for diagnostics.
    right_corners = np.float32(
        [[0, 0], [right_width, 0], [right_width, right_height], [0, right_height]]
    ).reshape(-1, 1, 2)
    corner_denominators = np.c_[right_corners.reshape(-1, 2), np.ones(4)] @ homography[2, :]
    render_transform = homography
    if corner_denominators.min() <= 0 <= corner_denominators.max():
        affine, affine_inliers = cv2.estimateAffinePartial2D(
            POINTS_RIGHT,
            POINTS_LEFT,
            method=cv2.RANSAC,
            ransacReprojThreshold=5.0,
        )
        affine_inlier_count = int(affine_inliers.sum()) if affine_inliers is not None else 0
        if affine is None or affine_inlier_count < 4:
            print(
                "The homography horizon crosses the right image, and too few landmark pairs "
                "support a stable full-frame similarity transform. Check the landmark pairs.",
                file=sys.stderr,
            )
            return 1
        render_transform = np.vstack((affine, [0.0, 0.0, 1.0]))
        print(
            "The homography horizon crosses the right image; rendering with a robust "
            f"similarity transform instead ({affine_inlier_count} inliers)."
        )

    # Find the union of the left image and the complete transformed right
    # image so the output shows the left view first and the right view beside it.
    projected_corners = cv2.perspectiveTransform(right_corners, render_transform).reshape(-1, 2)
    if not np.isfinite(projected_corners).all():
        print("The transform projects image corners to non-finite coordinates.", file=sys.stderr)
        return 1

    min_x = min(0, int(np.floor(projected_corners[:, 0].min())))
    min_y = min(0, int(np.floor(projected_corners[:, 1].min())))
    max_x = max(left_width, int(np.ceil(projected_corners[:, 0].max())))
    max_y = max(left_height, int(np.ceil(projected_corners[:, 1].max())))
    canvas_width = max_x - min_x
    canvas_height = max_y - min_y
    if canvas_width <= 0 or canvas_height <= 0 or canvas_width * canvas_height > 250_000_000:
        print(
            f"The estimated panorama canvas is unreasonable ({canvas_width}x{canvas_height}). "
            "Check the landmark pairs and homography.",
            file=sys.stderr,
        )
        return 1

    translation = np.array(
        [[1.0, 0.0, -min_x], [0.0, 1.0, -min_y], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    canvas_transform = translation @ render_transform
    # Save the right image by itself, cropped to its transformed bounds. Keep
    # a second copy on the shared panorama canvas for preview composition.
    right_min_x = int(np.floor(projected_corners[:, 0].min()))
    right_min_y = int(np.floor(projected_corners[:, 1].min()))
    right_max_x = int(np.ceil(projected_corners[:, 0].max()))
    right_max_y = int(np.ceil(projected_corners[:, 1].max()))
    right_canvas_width = right_max_x - right_min_x
    right_canvas_height = right_max_y - right_min_y
    right_translation = np.array(
        [[1.0, 0.0, -right_min_x], [0.0, 1.0, -right_min_y], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    warped_right = cv2.warpPerspective(
        right,
        right_translation @ render_transform,
        (right_canvas_width, right_canvas_height),
    )
    warped_right_on_panorama = cv2.warpPerspective(
        right, canvas_transform, (canvas_width, canvas_height)
    )
    left_on_canvas = np.zeros((canvas_height, canvas_width, 3), dtype=left.dtype)
    left_x, left_y = -min_x, -min_y
    left_on_canvas[left_y:left_y + left_height, left_x:left_x + left_width] = left

    # Use the left camera wherever it covers the canvas, and fill only the
    # remaining canvas area with pixels from the warped right camera.
    right_mask = cv2.warpPerspective(
        np.full((right_height, right_width), 255, dtype=np.uint8),
        canvas_transform,
        (canvas_width, canvas_height),
        flags=cv2.INTER_NEAREST,
    ) > 0
    left_mask = np.zeros((canvas_height, canvas_width), dtype=bool)
    left_mask[left_y:left_y + left_height, left_x:left_x + left_width] = True
    preview = left_on_canvas.copy()
    preview[right_mask & ~left_mask] = warped_right_on_panorama[right_mask & ~left_mask]
    if args.overlap_mode == "blend":
        overlap = left_mask & right_mask
        preview[overlap] = cv2.addWeighted(
            left_on_canvas[overlap], 0.5, warped_right_on_panorama[overlap], 0.5, 0
        )
    print(f"Panorama canvas: {canvas_width}x{canvas_height}")

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
    cv2.imshow("Full right image warped onto panorama canvas", warped_right)
    cv2.imshow("Alignment preview", preview)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
