# Step 08b: Remove Uninteresting Video Sections

## Purpose

Create a version of the Step 07a stitched video with the removal intervals selected in Step 08a removed. Keep the original video unchanged and produce a playable MP4 containing the remaining material in chronological order.

## Inputs

- By default, read the repository-root `settings.json`. Accept `--settings PATH` to use another settings JSON; relative paths resolve from the repository root. Use the selected file's `ScenarioName` and `ResultsDirectory` for default video, CSV, and output paths. Relative `ResultsDirectory` paths resolve from the repository root.
- By default, read `ResultsDirectory/Step07a_StitchedVideos/{ScenarioName}_Stitched.mp4`. Accept `--video PATH` to select another video; resolve relative paths from the repository root. Existing files under `Step07_StitchedVideos` are not moved; pass their path with `--video` to continue using one.
- By default, read `ResultsDirectory/Step08a_IdentifyRemovals/{ScenarioName}_RemovalSegments.csv`. Accept `--removals PATH` to select a CSV that matches the selected video; resolve relative paths from the repository root. Existing files in the former `Step08_IdentifyRemovals` folder are not moved or selected automatically.
- Require the Step 08a CSV header `ID,StartTime,EndTime,Comment`. Parse times as `HH:MM:SS.mmm` from the start of the video. Comments and IDs do not affect cut timing.

## Processing

- Validate that the input video is readable and has a video stream with positive duration, dimensions, and frame rate. Probe whether an audio stream is present.
- Validate every CSV row: four columns, parseable start and end times, a nonnegative start before the original end, and no overlaps after duration handling. If an end time exceeds the video duration, clamp that interval's effective end to the video duration before overlap checks and edge trimming; leave the CSV unchanged and report the adjustment. Reject a row if its start is at or beyond the video duration, because clamping would make the interval empty. Report malformed rows by row number.
- Interpret removal intervals as start-inclusive and end-exclusive. Preserve the intervals between removals, in order, using the source media timeline for both video and audio cuts. Reset segment timestamps before joining so the output plays continuously without gaps.
- To avoid retaining a very short opening fragment, if the first removal interval starts within 5 seconds of the video beginning (including exactly 5 seconds), extend that interval to start at time zero. Likewise, if the last removal interval ends within 5 seconds of the video duration, extend it through the end of the video. Apply these edge extensions to the effective removal intervals only; do not modify the Step 08a CSV.
- Calculate and report removed and retained durations using the effective intervals after edge extensions. A header-only CSV has no first or last interval, so no edge extension applies.
- Show console progress during processing, including completed percentage, processed output time, elapsed time, and estimated remaining time when available. Report the chosen input video, CSV, removed duration, retained duration, and whether audio is present.
- If the CSV contains only its header, create a verified copy of the input without applying cuts and report that no sections were marked. Reject a CSV whose ranges remove the entire video or leave no playable output.

## Output and encoding

- Write `{ScenarioName}_SectionsRemoved.mp4` to `ResultsDirectory/Step08b_RemovedSections/`, creating the folder as needed.
- Match Step 07a's output settings: retain the source frame dimensions and frame rate; encode video as H.264 with `medium`, CRF 18, and `yuv420p`; encode audio as AAC at 192 kb/s and 48 kHz when the source contains audio. If the input is silent, produce a silent MP4.
- Encode cuts with the same effective boundaries on video and audio. Verify the output is readable, has the expected dimensions, frame rate, and audio-stream presence, and has duration equal to the source duration minus the effective removed intervals within one source video frame per trim boundary.
- Encode to a temporary file in the output folder. Replace the scenario output only after encoding and verification succeed. On failure, remove only the temporary file and retain any previous valid output, the source video, CSV, and unrelated files.

## Run

Keep the reusable implementation in `Utilities/videoAlignment/step08b_remove_sections.py` and the launcher in `Workflow/Step08b_RemoveSections.py`. Run from the repository root with the `VideoStitcher` Conda environment active:

```powershell
python Workflow/Step08b_RemoveSections.py
```

Select another source video and its matching Step 08a CSV:

```powershell
python Workflow/Step08b_RemoveSections.py --video "path/to/video.mp4" --removals "path/to/removal_segments.csv"
```

The launcher may also accept `--video` alone when the configured scenario's Step 08a CSV is intended for that video.

Use a user-selected settings file:

```powershell
python Workflow/Step08b_RemoveSections.py --settings "settings_26_11_01_Soccer_WssaU14.json"
```

Options can be combined, for example:

```powershell
python Workflow/Step08b_RemoveSections.py --settings "settings_26_11_01_Soccer_WssaU14.json" --video "path/to/video.mp4" --removals "path/to/removal_segments.csv"
```

## Validation and failure handling

- Give actionable errors for missing settings, video, or CSV; unreadable or unsupported media; malformed CSV; invalid, overlapping, or out-of-duration ranges; no retained video; encoding failures; and output verification or write failures.
- Report the failing file or CSV row where possible. Never alter the input video or removal CSV.
- Reruns replace only `{ScenarioName}_SectionsRemoved.mp4`; preserve unrelated files in the Step 08b output folder.

## Acceptance criteria

- Defaults use the default or selected `--settings PATH` file's scenario and results directory to select the Step 07a stitched video and matching Step 08a CSV; `--video` and `--removals` select explicit inputs.
- Every marked interval is removed from both audio and video, and all retained sections are joined in their original order with continuous output timestamps.
- Opening or ending gaps of 5 seconds or less are removed by extending the first or last interval to the corresponding video edge; gaps longer than 5 seconds remain. This does not change the CSV or source video.
- Any removal end time beyond the video duration is treated as the video duration for processing and duration calculations; the CSV remains unchanged. A range that becomes empty after clamping is rejected.
- Output video dimensions, frame rate, H.264 quality settings, and audio behavior match Step 07a. A source without audio yields a silent output.
- Progress is visible for long encodes. A valid output replaces the prior scenario file only after verification; failures preserve all previous outputs and source files.
