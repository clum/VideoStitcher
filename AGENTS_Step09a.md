# Step 09a: Identify Region of Interest

## Purpose

Let the user extract and inspect a still frame from the Step 08b sections-removed video, then select a rectangular region of interest (ROI) with two mouse clicks. Save the source frame details and pixel rectangle as a JSON record without modifying the video.

## Inputs

- By default, read the repository-root `settings.json`. Accept `--settings PATH` to use another settings JSON; resolve a relative settings path from the repository root. Use its `ScenarioName` and `ResultsDirectory`; resolve a relative `ResultsDirectory` from the repository root.
- By default, open `ResultsDirectory/Step08b_RemovedSections/{ScenarioName}_SectionsRemoved.mp4`. Accept `--video PATH` to select another video when needed; resolve a relative path from the repository root.
- Validate that the selected video can be read and has a usable video stream, frame rate, dimensions, and duration.

## GUI behavior

- Create `Workflow/Step09a_IdentifyRegionOfInterest.py` as the user-facing launcher and place reusable behavior under `Utilities/`.
- Provide a video preview with play, pause, and seek controls. Show the current media timestamp. Let the user capture the current frame as a still image for ROI selection; display the captured frame at a size that fits the GUI while preserving its aspect ratio.
- Map clicks on the displayed frame to integer pixel coordinates in the original extracted frame, accounting for any scaling or padding. Ignore clicks outside the displayed image.
- The first valid click sets the ROI's top-left corner. The second valid click sets its bottom-right corner and completes the rectangle. Display both corner markers and the rectangle overlay. Require bottom-right `x` and `y` to be greater than the top-left values so the ROI has nonzero width and height.
- After both corners are selected, let the user click and drag either corner marker to refine the ROI. Update the marker and outlined rectangle continuously as the corner moves, and keep the ROI valid by constraining a dragged corner so it cannot cross the other corner or create a zero-width or zero-height rectangle. Save the final adjusted coordinates. Provide a way to clear the selection and start again.
- Require the final inclusive ROI width and height to be divisible by 2. After the second corner is selected and after each corner drag ends, automatically expand any odd dimension by one pixel. Expand the right or bottom edge when there is room; at the frame boundary, expand the left or top edge instead. Update the markers and rectangle to show the expanded ROI and report the adjustment to the user.
- If an odd dimension cannot be expanded without leaving the source frame, keep Save disabled and ask the user to adjust a corner until the dimension is even. Never extend the crop beyond the frame.
- Display the extracted frame timestamp and original frame dimensions. Do not resize, crop, or alter the source video or the coordinate basis used for saving.

## Output and persistence

- Save a complete ROI record as JSON under `ResultsDirectory/Step09a_IdentifyRegionOfInterest/`, creating the folder as needed.
- Use a scenario-based filename with a unique timestamp, such as `{ScenarioName}_RegionOfInterest_{timestamp}.json`, so saving a new selection does not overwrite an unrelated record.
- Record `ScenarioName`, source video path, captured frame timestamp in seconds, original frame width and height, and the final top-left and bottom-right pixel coordinates. Coordinates are zero-based pixel indices from the frame's top-left corner, with `x` increasing rightward and `y` increasing downward. Treat both endpoints as included in the rectangle; derived width and height are `bottom_right - top_left + 1` and must both be even.
- Save only after the user explicitly requests Save and both corners define a non-empty rectangle. Write atomically and report the full destination path. Do not create a persistent still-image output as part of this step; the still is for GUI selection and its metadata is recorded in the JSON.

## Run

Activate the `VideoStitcher` Conda environment and run from the repository root:

```powershell
conda activate VideoStitcher
python Workflow/Step09a_IdentifyRegionOfInterest.py
```

Use a selected settings file or video:

```powershell
python Workflow/Step09a_IdentifyRegionOfInterest.py --settings "settings_26_11_01_Soccer_WssaU14.json" --video "path/to/sections_removed.mp4"
```

Relative `--settings` and `--video` paths resolve from the repository root.

## Validation and failure handling

- Report actionable errors for missing or invalid settings, an invalid `ScenarioName` or `ResultsDirectory`, missing or unreadable video, failed frame extraction, invalid timestamp, clicks outside the image, invalid rectangle corners, and JSON write failures.
- Keep the last successfully extracted frame and current ROI selection intact if a later seek or extraction fails. Clear or replace the ROI only when the user explicitly captures a new frame or clears the selection.
- Preserve the source video and existing ROI records on all failures. A failed save must not leave a truncated JSON file.

## Acceptance criteria

- The default source is the configured Step 08b sections-removed video; `--settings` and `--video` allow explicit inputs.
- The user can seek to and capture a still frame, then mark the top-left and bottom-right corners in order.
- After completing the rectangle, the user can drag either corner control point and see the outline update; the saved coordinates match the final marker positions.
- Every saved ROI has an even width and height. Odd dimensions are expanded by one pixel within the frame, using the opposite edge when the right or bottom edge is already at the frame boundary.
- Click coordinates remain aligned with source-frame pixels when the preview is resized; padding clicks do not create points.
- A complete selection saves one JSON record under `ResultsDirectory/Step09a_IdentifyRegionOfInterest/` with scenario, source video, frame timestamp and dimensions, and both corner coordinates.
- Incomplete or invalid selections cannot be saved, and source videos and existing ROI records remain unchanged.
