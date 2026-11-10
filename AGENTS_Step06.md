# Step 06: Apply Homography and Stitch Images

## Purpose

Apply the homography computed in Step 05 to the corresponding right image, create a full-scene preview with the left image, and save both results.

## Inputs

- Read the repository-root `settings.json` and resolve `ResultsDirectory` using the shared workflow path rules.
- Load `ResultsDirectory/Step05_Homography/{ScenarioName}_Homography.json`. Require a matching scenario, finite invertible 3x3 `HomographyMatrix`, and valid `CoordinateRecordPath`.
- Load the exact Step 04 coordinate record referenced by the homography artifact. Validate scenario, point pairs, image paths, recorded dimensions, and point bounds; decode both images and ensure actual dimensions match the record.
- The matrix maps right-image pixels to left-image coordinates. Use it directly unless its projective horizon crosses the right image; in that case use the established RANSAC similarity-transform fallback for rendering to avoid a disconnected full-frame warp.

## Stitching behavior

- Compute a canvas containing the full left image and the complete transformed right image. Save `warped_right.jpg` cropped to the transformed right image's bounds.
- Build `alignment_preview.jpg` on the shared canvas. Default to left-camera pixels wherever the left image is present and use warped right pixels only to fill uncovered areas. Support equal-weight overlap blending with `--overlap-mode blend`; `--overlap-mode left` is the default.
- Save outputs under `ResultsDirectory/Step06_StitchedImages/` as `warped_right.jpg` and `alignment_preview.jpg`. Stage both images before replacing the previous Step 06 pair, and preserve unrelated files.
- After saving, display the left image, warped right image, and alignment preview in OpenCV windows. Press a key in a preview window to close them. Activate the `VideoStitcher` Conda environment, which provides OpenCV GUI support, before running this step.

## Run

From the repository root:

```powershell
python Workflow/Step06_StitchImages.py
```

Select overlap blending with:

```powershell
python Workflow/Step06_StitchImages.py --overlap-mode blend
```

## Failure handling

- Report actionable errors for missing or malformed Step 05 artifacts, scenario mismatch, missing or malformed coordinate records, invalid matrices, unreadable images, dimension mismatches, invalid points, transform failures, unreasonable canvas sizes, and output write failures.
- Do not publish only one updated image if staging or publishing the pair fails. Do not modify source images or unrelated result files.

## Acceptance criteria

- Step 06 loads the matrix and coordinate-record path produced by Step 05 and applies them to the same image pair and point set.
- Both images are written to the configured Step 06 result folder and contain the complete warped right image and full-scene preview.
- Left-priority and blend overlap modes work, with left-priority as the default; `warped_right.jpg` is independent of overlap mode.
- Invalid inputs and output failures are actionable and do not leave a partially updated output pair.
- Reruns replace only the two Step 06 outputs and preserve source media and unrelated results.
