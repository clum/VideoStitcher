# Step 05: Compute Homography

## Purpose

Estimate the homography that maps right-image landmark coordinates to their corresponding left-image coordinates. This step computes and saves the transform only; Step 06 applies it to the images.

## Inputs

- By default, read the repository-root `settings.json`. Accept `--settings PATH` to use another settings JSON; relative paths resolve from the repository root. Use the selected file's `ScenarioName` and `ResultsDirectory` for coordinate-record selection and homography output. Relative `ResultsDirectory` paths resolve from the repository root.
- By default, load the lexicographically newest `{ScenarioName}_Coordinates_*.json` from `ResultsDirectory/Step04_ImageCoordinates/`. Step 04 filenames include a sortable timestamp.
- Accept `--coordinates PATH` to use a specific Step 04 record instead. Resolve relative paths from the repository root.
- Read `Left.ImagePath`, `Right.ImagePath`, `Left.Points`, and `Right.Points` from that record. Each point is an integer `{ "x": ..., "y": ... }` pair, and both lists must contain at least four corresponding points in the same order.
- The coordinate record's `ScenarioName` must match the configured scenario. Validate positive recorded dimensions and ensure every point is an integer within its recorded image bounds. Step 05 does not decode or modify the source images.

## Homography behavior

- Estimate a RANSAC homography from `Right.Points` to `Left.Points` using a 5-pixel reprojection threshold. Print the matrix and inlier count.
- Save the matrix and provenance in `ResultsDirectory/Step05_Homography/{ScenarioName}_Homography.json`. Include the scenario, selected coordinate-record path, local computation time, RANSAC inlier count, point-pair count, and threshold.
- Replace only the current scenario's homography artifact. Stage the JSON and replace it only after estimation and serialization succeed; preserve unrelated files.

## Run

From the repository root:

```powershell
python Workflow/Step05_ComputeHomography.py
```

Use a specific coordinate record with:

```powershell
python Workflow/Step05_ComputeHomography.py --coordinates Outputs/Step04_ImageCoordinates/26_10_31_Mukilteo_Coordinates_20261107_231421_060166.json
```

Use a user-selected settings file:

```powershell
python Workflow/Step05_ComputeHomography.py --settings "settings_26_11_01_Soccer_WssaU14.json"
```

Options can be combined to select both a settings file and a coordinate record.

## Failure handling

- Report actionable errors for unreadable settings or coordinate records, no matching Step 04 record, a scenario mismatch, invalid dimensions, malformed or out-of-bounds points, mismatched lists, transform failures, and output write failures.
- Do not replace the previous homography artifact if validation or estimation fails. Do not modify source images or unrelated results.

## Acceptance criteria

- The workflow can run from the repository root and defaults to the newest matching Step 04 coordinate record; an explicit `--coordinates` path selects the requested record.
- A valid run writes a finite 3x3 homography plus its provenance to the configured Step 05 result folder.
- The selected coordinate-record path in the artifact lets Step 06 apply the matrix to the exact image pair and landmarks used to estimate it.
- Invalid records, points, transforms, and output operations fail with actionable messages and do not replace the prior artifact.
- Reruns replace only the current scenario's Step 05 artifact and preserve source media and unrelated results.
