# Video Stitcher

## Purpose

Build a Python desktop application that helps a user manually align two recordings of the same event, filmed from different angles. The primary result is the signed time offset to apply to the second video so its content aligns with the first. The app also determines the matching start and end trims needed to make both aligned clips exactly the same duration.

## User workflow

1. On startup, read the repository-root `settings.json` and locate each combined input in `ResultsDirectory/Step01_CombinedVideos/` using the sanitized `ScenarioName` and camera name. Resolve relative `ResultsDirectory` values from the repository root. Show the full resolved path for each input in the interface. Load `CameraBOffset_s` as the initial signed offset. Keep controls for changing either input during the session. Do not modify source files.
2. Read each file's duration, frame rate or timestamp information, dimensions, and audio stream availability. Show clear errors for unreadable files and warn when either clip has no audio.
   - Limit interactive preview and manual alignment to the first 30 seconds of each file. Show the user that the rest of each source clip is outside the current review window. Export still uses each configured source file's full duration.
3. Display both videos and their audio waveforms in a common timeline. Make the waveforms easy to compare, with distinct styles or colors for A and B. Provide a timeline zoom control centered on the current playhead so the user can make fine alignment adjustments. Allow the user to drag the second clip earlier or later relative to the first. Moving the second clip must move its video and audio waveform together. Keep the reference clip fixed.
4. Let the user play or scrub both clips while they are aligned on the timeline, so visual events and sound can be compared. Show the current offset with sufficient precision and make the drag interaction usable for fine adjustments.
5. Once the user accepts the alignment, calculate and report the interactive 30-second review overlap. For export, calculate the shared interval and start/end crop amounts against both original files' full durations. Both output clips must cover the same event interval and have equal duration.
6. Present the result clearly, with the signed offset in seconds and a human-readable time format. Let the user save the current offset to `CameraBOffset_s` in the repository-root `settings.json`, preserving the other settings.
7. Provide a control to export the current alignment. Apply the current signed offset to B, use the full durations of the combined Step 01 inputs, crop A and B to their shared interval, and write an equal-duration MP4 pair under `ResultsDirectory/Step02_AlignedVideos/`. Prefix both filenames with the sanitized `ScenarioName` from `settings.json`. Reuse `{ScenarioName}_CameraA_Aligned.mp4` and `{ScenarioName}_CameraB_Aligned.mp4` on every run, replacing that pair only after both temporary outputs are encoded and validated. Preserve unrelated files and numbered legacy variants. The 30-second limit applies only to interactive alignment and preview, not to export.

## Offset and crop conventions

- Let `A` be the first video's timeline start and set it to time `0`.
- Let `offset` be the second video's start time on A's timeline. A positive value means the second video starts later than A; a negative value means it starts earlier. Applying this offset means delaying or advancing the second video's timeline by that signed amount.
- Let `duration_a` and `duration_b` be the input durations. The shared interval on A's timeline is:
  - `common_start = max(0, offset)`
  - `common_end = min(duration_a, offset + duration_b)`
  - `common_duration = max(0, common_end - common_start)`
- Crop A by `common_start` at the beginning and `duration_a - common_end` at the end.
- Crop B by `common_start - offset` at the beginning and `(offset + duration_b) - common_end` at the end.
- If `common_duration` is zero, the clips do not overlap at the chosen offset; explain this and do not present valid matching trims.
- Be explicit that an offset is not itself a crop amount. It is the signed timeline shift to apply to B; trims select the common interval after alignment.

## Product and implementation guidance

- Keep the interface focused on selecting two files, comparing synchronized playback, adjusting the second clip, and reading the resulting offset and trims.
- Provide `Workflow/Step01_CombineRawVideos.py` to combine the supported video files directly inside each camera's `RawVideoFilePath` folder. Print the full list of files before combining each camera and show a console progress bar for the complete two-camera operation. Order by modification time oldest-first, using natural filename order for ties; stream-copy video and audio while omitting unsupported ancillary camera data streams, and preserve the source extension. Stage both `{ScenarioName}_CameraA` and `{ScenarioName}_CameraB` outputs before replacing the fixed pair in `ResultsDirectory/Step01_CombinedVideos/`.
- Keep the Step 02 launcher at `Workflow/Step02ApplyTimeOffset.py`; it invokes the main application entry point.
- Encapsulate application behavior in callable Python scripts, functions, classes, and/or libraries. Store this code under `Utilities/` in an organized package structure, with the user interface calling reusable functionality where practical.
- Define a stable main application entry point that can be called from the Step 02 launcher and normal application startup.
- Show determinate export progress from FFmpeg's encoded timestamps across both outputs, reaching 100% only after validation and publishing succeed.
- Capture FFmpeg diagnostics to a run-specific log for failed exports, and include the log path and recent error output in the app's failure message. Remove temporary logs after a successful export.
- Preserve source media. Export must write new files and must use the calculated common interval for both clips.
- Use a media playback approach that supports both video and audio and handles differing frame rates, stream timestamps, and audio sample rates. Keep playback position and offset calculations based on media timestamps rather than assuming both videos have identical frame rates.
- Visualize audio as an amplitude waveform on each clip's timeline row; do not represent audio only with a dashed line. Build the waveform from the active first-30-second window and keep it attached to its video when the second clip moves.
- Use precise internal time values (for example, integer stream time-base ticks or rational values where practical). Round only for display or when an export format requires frame boundaries. Explain any frame-boundary rounding in the result.
- Encode both exported videos at the same output frame rate and frame count so their video durations match exactly. Report the resulting frame-rounded duration to the user.
- Keep media probing, offset/overlap calculations, and UI behavior in separable modules so the calculations can be maintained independently of the interface.
- Handle paths with spaces and Unicode, long clips, seeking, paused playback, and missing audio without silently shifting the requested offset.
- Do not infer or automatically change the user's chosen alignment. The user controls the second clip's position; any automatic assistance must remain optional and must not replace the displayed manual offset.

## Completion criteria

- The user can load two videos and compare their video and audio during playback.
- Dragging the second video changes its signed offset while keeping its audio attached.
- The app displays the exact signed offset and derives the shared interval and both clips' beginning and ending crop amounts consistently.
- The resulting clips' requested time span is equal, and the app reports when the selected offset leaves no shared interval.
- Export creates a same-duration pair under `ResultsDirectory/Step02_AlignedVideos/` with stable `ScenarioName`-prefixed filenames, overwriting only that pair while preserving source files and unrelated results.
- Original files remain untouched.
