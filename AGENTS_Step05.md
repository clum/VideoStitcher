# Step 05: Stitch Selected Images

## Purpose

Use the corresponding image paths and landmark coordinates saved by Step 04 to align the right image, create a full-scene preview, and save both outputs. This step consumes source images without modifying them.

## Inputs

- Read the repository-root `settings.json` and resolve `ResultsDirectory` using the shared workflow path rules.
- By default, load the lexicographically newest `{ScenarioName}_Coordinates_*.json` from `ResultsDirectory/Step04_ImageCoordinates/`. Step 04 filenames include a sortable timestamp.
- Accept `--coordinates PATH` to use a specific Step 04 record instead. Resolve relative paths from the repository root.
- Read `Left.ImagePath`, `Right.ImagePath`, `Left.Points`, and `Right.Points` from that record. Each point is an integer `{ "x": ..., "y": ... }` pair, and both lists must contain at least four corresponding points in the same order.
- The coordinate record's `ScenarioName` must match the configured scenario. Each image must exist and decode, the recorded dimensions must match the decoded images, and every point must fall within its image bounds.

## Stitching behavior

- Estimate a RANSAC homography that maps right-image coordinates to left-image coordinates, using a 5-pixel reprojection threshold. Print the matrix and inlier count.
- If the homography's projective horizon crosses the right image, use a robust similarity transform for rendering so the full image does not fold into disconnected regions. Report this fallback; fail clearly if fewer than four pairs support it.
- Calculate a canvas containing the left image and the transformed right image. Save `warped_right.jpg` cropped to the transformed right image's bounds.
- Build `alignment_preview.jpg` on the shared canvas. Default to left-camera pixels wherever the left image is present, filling uncovered areas with warped right pixels. Support equal-weight overlap blending with `--overlap-mode blend`; `--overlap-mode left` is the default.
- Save outputs under `ResultsDirectory/Step05_StitchedImages/` as `warped_right.jpg` and `alignment_preview.jpg`. Stage both images before replacing the previous Step 05 pair, and preserve unrelated files.
- After saving, display the left image, warped right image, and alignment preview in OpenCV windows. Press a key in a preview window to close them. Activate the `VideoStitcher` Conda environment, which provides OpenCV GUI support, before running the step.

## Run

From the repository root:

```powershell
python Workflow/Step05_StitchImages.py
```

Use a specific coordinate record and/or overlap mode with:

```powershell
python Workflow/Step05_StitchImages.py --coordinates Outputs/Step04_ImageCoordinates/26_10_31_Mukilteo_Coordinates_20261107_231421_060166.json --overlap-mode blend
```

## Failure handling

- Report actionable errors for unreadable settings or coordinate records, no matching Step 04 record, a scenario mismatch, missing or unreadable images, invalid dimensions, malformed or out-of-bounds points, mismatched lists, transform failures, unreasonable canvas sizes, and output write failures.
- Do not publish only one updated image if staging or publishing the output pair fails. Do not alter the source images or unrelated result files.

## Acceptance criteria

- The workflow can run from the repository root and defaults to the newest matching Step 04 coordinate record; an explicit `--coordinates` path selects the requested record.
- Both outputs are created in the configured Step 05 result folder and contain the complete transformed right image and full-scene preview.
- Left-priority and blend overlap modes work, with left-priority as the default; `warped_right.jpg` is independent of overlap mode.
- Invalid records, images, points, transforms, and output operations fail with actionable messages and do not leave a partially updated output pair.
- Reruns replace only the two Step 05 outputs and preserve source media and unrelated results.
