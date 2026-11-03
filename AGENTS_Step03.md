# Step 03: Extract Aligned Video Images

## Purpose

Extract representative still frames from both Step 02 aligned videos so the user can review the same timeline points from Camera A and Camera B.

## Inputs

- Read the repository-root `settings.json`.
- Resolve `ResultsDirectory` from the repository root when it is relative.
- Read the fixed Step 02 outputs from `ResultsDirectory/Step02_AlignedVideos/`:
  - `{ScenarioName}_CameraA_Aligned.mp4`
  - `{ScenarioName}_CameraB_Aligned.mp4`
- Use the sanitized `ScenarioName` convention shared by the other workflow steps.

## Extraction behavior

- Extract exactly one frame from each camera video at 0, 25, 50, 75, and 100 seconds.
- For each selected frame, write JPEG, PNG, and BMP versions. Preserve the corresponding source video's native width and height; do not resize or crop.
- Select the decoded frame whose presentation timestamp is closest to each requested time. Encode that same selected frame into all three image formats.
- Produce exactly 30 images total: five timestamps x two cameras x three formats. Use lowercase `.jpg`, `.png`, and `.bmp` extensions.
- Name files with the sanitized scenario, source camera, and timestamp, for example `{ScenarioName}_CameraA_t025s.jpg`.
- Write outputs to `ResultsDirectory/Step03_ExtractedImages/`.

## Validation and publishing

- Validate that both source files are readable, every requested timestamp has a decodable frame, each image has the expected format and native dimensions, and all 30 outputs were created.
- If a video is too short or an extraction/validation fails, report the camera and timestamp clearly and do not publish a partial set.
- Stage and validate the complete set before publishing it. Repeated runs reuse the same 30 filenames and replace only those Step 03 outputs; preserve source videos and unrelated files in the results folder.
- Create `Workflow/Step03_ExtractAlignedImages.py` as the runnable script that executes this workflow from the repository root with `python Workflow/Step03_ExtractAlignedImages.py`.
- Keep reusable extraction logic under `Utilities/`; the workflow script should call that reusable implementation.

## Acceptance criteria

- The output folder contains exactly five requested timestamps for each camera in each of the three formats.
- All stills retain their source camera's dimensions, and the three formats for a camera/timestamp represent the same selected frame.
- A repeated run produces the same names and replaces the Step 03 image set without changing inputs or unrelated results.
- Failures identify the affected input and time and do not leave a partially published image set.
