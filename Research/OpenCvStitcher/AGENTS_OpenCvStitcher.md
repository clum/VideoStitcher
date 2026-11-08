# OpenCV Two-Image Alignment with Manual Landmarks

## Goal

Use OpenCV to estimate a perspective transform from manually matched landmarks, warp the right camera image into the left image's coordinate system, and inspect a blended preview. This is a research workflow, separate from the production video steps.

## Inputs and outputs

- Default images: `26_10_31_Mukilteo_CameraA_t050s.png` as the left image and `26_10_31_Mukilteo_CameraB_t050s.png` as the right image.
- Default corresponding landmarks are in `POINTS_LEFT` and `POINTS_RIGHT` near the top of `stitch_two_images.py`. Replace them with coordinates from the selected image pair. The two lists must use the same point order.
- Each coordinate is `(x, y)` in pixels from that image's top-left corner. The script validates that there are at least four pairs, both lists have equal lengths, and all points are in their respective image bounds.
- Output files are `warped_right.jpg` and `alignment_preview.jpg` in `Research/OpenCvStitcher/` by default. The preview window shows the left frame, warped right frame, and equal-weight blend.
- The output canvas has the left image's dimensions. Warped content outside that canvas is clipped.
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

The script prints the homography matrix and RANSAC inlier count, saves both outputs, then opens the three preview windows. Press a key while a preview window is active to close them.

## Workflow

1. Load both frames in color with OpenCV.
2. Identify at least four corresponding visual landmarks and enter them in matching order in `POINTS_LEFT` and `POINTS_RIGHT`.
3. Call `cv2.findHomography(POINTS_RIGHT, POINTS_LEFT, cv2.RANSAC, 5.0)` to estimate a transform that maps right-image coordinates into the left image.
4. Warp the right image with `cv2.warpPerspective` to the left image's width and height.
5. Blend the left and warped-right images at equal weights with `cv2.addWeighted`, save both images, and inspect the preview for alignment errors.

## Practical notes

- Choose distinctive landmarks spread across the shared area. Avoid points that are ambiguous, moving, or nearly on one line.
- Keep corresponding points in exactly the same order. A wrong pair can distort the estimated transform; RANSAC may reject outliers, but it cannot repair incorrect matches generally.
- A homography approximates a planar scene or camera rotation. Parallax from nearby objects can leave some regions misaligned even when the chosen points match.
- The blend is a quick alignment check. Equal-weight blending can ghost edges and darken areas where the warped image has no content.
- If the output needs to contain all of both images, compute a larger output canvas and translation before warping; this example intentionally uses the left frame's dimensions.
