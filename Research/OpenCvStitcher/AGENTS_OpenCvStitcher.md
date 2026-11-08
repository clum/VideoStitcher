# OpenCV Two-Image Alignment with Manual Landmarks

## Goal

Use OpenCV to estimate a perspective transform from manually matched landmarks, warp the right camera image into the left image's coordinate system, and inspect a blended preview. This is a research workflow, separate from the production video steps.

## Inputs and outputs

- Default images: `26_10_31_Mukilteo_CameraA_t050s.png` as the left image and `26_10_31_Mukilteo_CameraB_t050s.png` as the right image.
- Default corresponding landmarks are in `POINTS_LEFT` and `POINTS_RIGHT` near the top of `stitch_two_images.py`. Replace them with coordinates from the selected image pair. The two lists must use the same point order.
- Each coordinate is `(x, y)` in pixels from that image's top-left corner. The script validates that there are at least four pairs, both lists have equal lengths, and all points are in their respective image bounds.
- Output files are `warped_right.jpg` and `alignment_preview.jpg` in `Research/OpenCvStitcher/` by default. The preview overlap behavior is selectable with `--overlap-mode left` or `--overlap-mode blend`; left-priority is the default.
- `warped_right.jpg` contains the complete transformed right image cropped to its own projected bounds. `alignment_preview.jpg` uses a shared canvas large enough to contain both the full left image and all projected corners of the transformed right image; it places the left image on the left and the complete warped right image beside it.
- Original images are read only.

## Run

From the repository root:

```powershell
python Research/OpenCvStitcher/stitch_two_images.py
```

Pass another pair of image paths and an output folder with:

```powershell
python Research/OpenCvStitcher/stitch_two_images.py left.jpg right.jpg --output-dir results
```

Choose left-camera priority (the default) or equal-weight blending in the overlap with:

```powershell
python Research/OpenCvStitcher/stitch_two_images.py --overlap-mode left
python Research/OpenCvStitcher/stitch_two_images.py --overlap-mode blend
```

The script prints the homography matrix and RANSAC inlier count, saves both outputs, then opens the three preview windows. Press a key while a preview window is active to close them.

## Workflow

1. Load both frames in color with OpenCV.
2. Identify at least four corresponding visual landmarks and enter them in matching order in `POINTS_LEFT` and `POINTS_RIGHT`.
3. Call `cv2.findHomography(POINTS_RIGHT, POINTS_LEFT, cv2.RANSAC, 5.0)` to estimate a transform that maps right-image coordinates into the left image.
4. Check whether the homography's projective horizon crosses the right image. If it does, estimate a robust similarity transform from the point pairs for rendering; a horizon-crossing homography folds the full image into disjoint regions. The homography and its inlier count remain printed for diagnostics.
5. Project the right image's corners through the rendering transform, compute the union bounds with the left image, and translate the transform so both images fit on a positive-coordinate canvas.
6. Warp the full right image to that canvas. Save it as `warped_right.jpg`; the black area represents locations outside the transformed right image. Build `alignment_preview.jpg` on the same canvas. With `--overlap-mode left`, use left-camera pixels wherever the left image covers the canvas and warped right-camera pixels only to fill uncovered areas. With `--overlap-mode blend`, average the two images equally in the overlap.

## Practical notes

- Choose distinctive landmarks spread across the shared area. Avoid points that are ambiguous, moving, or nearly on one line.
- Keep corresponding points in exactly the same order. A wrong pair can distort the estimated transform; RANSAC may reject outliers, but it cannot repair incorrect matches generally.
- A homography approximates a planar scene or camera rotation. Parallax from nearby objects can leave some regions misaligned even when the chosen points match.
- Left-priority mode preserves the left camera in the overlap. Blend mode can ghost edges when corresponding pixels do not align; either mode can show a visible seam at the overlap boundary.
- Extreme or incorrect landmark geometry can project corners very far away or near the homography horizon. The script uses a similarity transform for a full-frame render when the horizon crosses the image, and rejects non-finite or excessively large output canvases. Inspect the point pairs if the similarity fallback has fewer than four inliers or the canvas is still unreasonable.
