# Step 08b: Remove Uninteresting Video Sections

## Purpose

Create a version of the Step 07 stitched video with the removal intervals selected in Step 08 removed. Keep the original video unchanged and produce a playable MP4 containing the remaining material in chronological order.

## Inputs

- Read repository-root `settings.json` and resolve `ScenarioName` and `ResultsDirectory` using the shared workflow path rules.
- By default, read `ResultsDirectory/Step07_StitchedVideos/{ScenarioName}_Stitched.mp4`. Accept `--video PATH` to select another video; resolve relative paths from the repository root.
- By default, read `ResultsDirectory/Step08_IdentifyRemovals/{ScenarioName}_RemovalSegments.csv`. Accept `--removals PATH` to select a CSV that matches the selected video; resolve relative paths from the repository root.
- Require the Step 08 CSV header `ID,StartTime,EndTime,Comment`. Parse times as `HH:MM:SS.mmm` from the start of the video. Comments and IDs do not affect cut timing.

## Processing

- Validate that the input video is readable and has a video stream with positive duration, dimensions, and frame rate. Probe whether an audio stream is present.
- Validate every CSV row: four columns, parseable start and end times, `0 <= start < end <= video duration`, and no overlaps. Reject malformed rows and report their row number; do not silently clip, merge, or reinterpret ranges.
- Interpret removal intervals as start-inclusive and end-exclusive. Preserve the intervals between removals, in order, using the source media timeline for both video and audio cuts. Reset segment timestamps before joining so the output plays continuously without gaps.
- To avoid retaining a very short opening fragment, if the first removal interval starts within 5 seconds of the video beginning (including exactly 5 seconds), extend that interval to start at time zero. Likewise, if the last removal interval ends within 5 seconds of the video duration, extend it through the end of the video. Apply these edge extensions to the effective removal intervals only; do not modify the Step 08 CSV.
- Calculate and report removed and retained durations using the effective intervals after edge extensions. A header-only CSV has no first or last interval, so no edge extension applies.
- Show console progress during processing, including completed percentage, processed output time, elapsed time, and estimated remaining time when available. Report the chosen input video, CSV, removed duration, retained duration, and whether audio is present.
- If the CSV contains only its header, create a verified copy of the input without applying cuts and report that no sections were marked. Reject a CSV whose ranges remove the entire video or leave no playable output.

## Output and encoding

- Write `{ScenarioName}_SectionsRemoved.mp4` to `ResultsDirectory/Step08b_RemovedSections/`, creating the folder as needed.
- Match Step 07's output settings: retain the source frame dimensions and frame rate; encode video as H.264 with `medium`, CRF 18, and `yuv420p`; encode audio as AAC at 192 kb/s and 48 kHz when the source contains audio. If the input is silent, produce a silent MP4.
- Encode cuts with the same effective boundaries on video and audio. Verify the output is readable, has the expected dimensions, frame rate, and audio-stream presence, and has duration equal to the source duration minus the effective removed intervals within one source video frame per trim boundary.
- Encode to a temporary file in the output folder. Replace the scenario output only after encoding and verification succeed. On failure, remove only the temporary file and retain any previous valid output, the source video, CSV, and unrelated files.

## Run

Keep the reusable implementation in `Utilities/videoAlignment/step08b_remove_sections.py` and the launcher in `Workflow/Step08b_RemoveSections.py`. Run from the repository root with the `VideoStitcher` Conda environment active:

```powershell
python Workflow/Step08b_RemoveSections.py
```

Select another source video and its matching Step 08 CSV:

```powershell
python Workflow/Step08b_RemoveSections.py --video "path/to/video.mp4" --removals "path/to/removal_segments.csv"
```

The launcher may also accept `--video` alone when the configured scenario's Step 08 CSV is intended for that video.

## Validation and failure handling

- Give actionable errors for missing settings, video, or CSV; unreadable or unsupported media; malformed CSV; invalid, overlapping, or out-of-duration ranges; no retained video; encoding failures; and output verification or write failures.
- Report the failing file or CSV row where possible. Never alter the input video or removal CSV.
- Reruns replace only `{ScenarioName}_SectionsRemoved.mp4`; preserve unrelated files in the Step 08b output folder.

## Acceptance criteria

- Defaults select the configured Step 07 stitched video and the matching Step 08 removal CSV; `--video` and `--removals` select explicit inputs.
- Every marked interval is removed from both audio and video, and all retained sections are joined in their original order with continuous output timestamps.
- Opening or ending gaps of 5 seconds or less are removed by extending the first or last interval to the corresponding video edge; gaps longer than 5 seconds remain. This does not change the CSV or source video.
- Output video dimensions, frame rate, H.264 quality settings, and audio behavior match Step 07. A source without audio yields a silent output.
- Progress is visible for long encodes. A valid output replaces the prior scenario file only after verification; failures preserve all previous outputs and source files.
