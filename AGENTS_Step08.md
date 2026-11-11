# Step 08: Identify Video Sections for Removal

## Purpose

Provide a desktop GUI for reviewing the stitched video from Step 07 and recording long, uninteresting intervals—such as halftime breaks or timeouts—for a later workflow step to remove. Step 08 only identifies intervals and writes a CSV; it must not modify or re-encode the video.

## Inputs

- Read repository-root `settings.json` and resolve `ScenarioName` and `ResultsDirectory` using the shared workflow path rules.
- By default, open `ResultsDirectory/Step07_StitchedVideos/{ScenarioName}_Stitched.mp4`. Accept `--video PATH` to specify an input video when launching Step 08; resolve relative paths from the repository root. If omitted, use the configured Step 07 output.
- Provide a Browse control so the user can select another supported video from the GUI, and display the full selected path. A GUI selection replaces the startup/default input for the current session.
- Report actionable errors when settings or the default video are missing, a specified or browsed path is invalid, or the video cannot be opened or probed. Do not alter the video.

## GUI behavior

- Implement a resizable Tkinter GUI, following the repository's existing workflow style. Provide video and audio playback, play/pause, a timeline scrubber, a visible current time, and controls to mark the current playhead position as the start or end of a removal interval. Keep audio synchronized with the selected video's timeline; pausing, stopping, or seeking must pause, stop, or seek audio to the same position.
- If the selected video has no audio stream, keep video playback and all interval controls available and clearly report that audio is unavailable.
- The preview may decode at a lower resolution and/or frame rate to keep navigation responsive. All displayed times and saved marks must remain on the original video's timeline and must not drift because of preview scaling, frame dropping, or seeking.
- Let the user complete a range with start time, end time, and a comment. Comments may be blank. Display ranges in an editable list; the user can select a range to modify its start, end, or comment, or remove it.
- Require `0 <= start < end <= video duration`. Reject ranges that overlap any other range, including during edits, and explain the conflict without silently changing either interval. Sort the list by start time and assign IDs sequentially from 1 in that order; update IDs after edits or removals.
- Make incomplete marks clear in the interface and do not save a half-entered interval.

## Output and persistence

- Save the CSV to `ResultsDirectory/Step08_IdentifyRemovals/{ScenarioName}_RemovalSegments.csv`, creating the folder as needed. Use an explicit Save action; saving replaces only the CSV for the configured scenario and preserves unrelated files.
- Write a header followed by one row per range, with exactly these columns in this order: `ID,StartTime,EndTime,Comment`.
- Format both times as `HH:MM:SS.mmm` from the original video's start. Quote and escape comments according to standard CSV rules so commas, quotes, and newlines are preserved as a single comment field.
- Sort rows by start time and use sequential integer IDs. An empty range list may be saved as a header-only CSV to represent that no sections were marked.
- Write safely so a failed save does not leave a truncated replacement. Do not produce modified video files or other media outputs.

## Run

From the repository root, with the `VideoStitcher` Conda environment active:

```powershell
python Workflow/Step08_IdentifyRemovals.py
```

Open a specific video instead of the configured Step 07 output:

```powershell
python Workflow/Step08_IdentifyRemovals.py --video "path/to/video.mp4"
```

Keep the user-facing entry point under `Workflow/` and reusable implementation under `Utilities/`, following the shared workflow conventions.

## Validation and failure handling

- Validate video readability, dimensions, frame rate, duration, and audio-stream availability before enabling range marking. Keep timeline seeking bounded to the source duration and report audio playback or decode failures clearly.
- Validate each range on creation and edit. Reject missing endpoints, non-finite or out-of-duration timestamps, zero-length or reversed ranges, and overlaps; preserve the last valid range when an edit fails.
- Keep GUI seeking responsive for long videos and report decode or seek failures clearly. Show the destination path and error on CSV write failure; preserve any prior valid CSV if saving fails.
- Close cleanly without changing the source video or saving unsaved changes silently.

## Acceptance criteria

- The GUI opens the configured Step 07 stitched video by default, `--video PATH` overrides that startup input, and the user can browse to another video in the GUI.
- The user can play, pause, and scrub synchronized video and audio, and mark interval endpoints. Videos without audio remain usable with a clear status. Lower-resolution or lower-frame-rate preview does not change the corresponding source timestamps.
- The user can add a comment, edit or remove any interval, and see ranges sorted by start time with sequential IDs. Invalid and overlapping ranges are rejected with an explanation.
- Saving writes the specified four-column CSV with a header, `HH:MM:SS.mmm` timestamps, escaped comments, and stable scenario-based filename under `ResultsDirectory/Step08_IdentifyRemovals/`.
- Save failures preserve a prior valid CSV. The source video remains unchanged, and no video output is created.
