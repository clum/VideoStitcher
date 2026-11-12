# Step 08: Identify Video Sections for Removal

## Purpose

Provide a resizable PySide6 GUI for reviewing the stitched video from Step 07 and recording long, uninteresting intervals—such as halftime breaks or timeouts—for a later workflow step to remove. Step 08 only identifies intervals and writes a CSV; it must not modify or re-encode the video.

## Inputs

- Read repository-root `settings.json` and resolve `ScenarioName` and `ResultsDirectory` using the shared workflow path rules.
- By default, open `ResultsDirectory/Step07_StitchedVideos/{ScenarioName}_Stitched.mp4`. Accept `--video PATH` to specify an input video when launching Step 08; resolve relative paths from the repository root. If omitted, use the configured Step 07 output.
- Provide a Browse control so the user can select another supported video from the GUI, and display the full selected path. A GUI selection replaces the startup/default input for the current session.
- Provide a Browse CSV control for choosing the output directory and filename. Start with the configured scenario-based CSV path. Selecting a new path keeps the current ranges and changes where the next explicit save writes.
- If the selected CSV already exists, load its ranges into the GUI for review and editing. Require the `ID,StartTime,EndTime,Comment` header and valid rows; validate timestamps, duration bounds, and overlaps against the currently loaded video. Reject the entire file on any error and preserve the current ranges and output path. Imported rows are sorted by start time and receive sequential IDs in the GUI.
- Report actionable errors when settings or the default video are missing, a specified or browsed path is invalid, or the video cannot be opened or probed. Do not alter the video.

## GUI behavior

- Use one PySide6 `QMediaPlayer` for both video and audio, rendered in a `QVideoWidget`. Use the player's reported media position as the single source for the current-time display, timeline scrubber, and interval endpoints. Play, pause, stop, and seeking must operate through this same player.
- Load media asynchronously. Keep playback and marking disabled until Qt reports the media ready and a valid duration. Report Qt Multimedia decoding or playback errors clearly.
- If the selected video has no audio stream, keep video playback and all interval controls available and clearly report that audio is unavailable.
- The Qt video widget scales to the available GUI space. The player position and saved marks always refer to the source media timeline; frame dropping or display scaling must not change timestamps.
- Let the user complete a range with start time, end time, and a comment. Comments may be blank. Display ranges in an editable list; the user can select a range to modify its start, end, or comment, or remove it.
- Require `0 <= start < end <= video duration`. Reject ranges that overlap any other range, including during edits, and explain the conflict without silently changing either interval. Sort the list by start time and assign IDs sequentially from 1 in that order; update IDs after edits or removals.
- Make incomplete marks clear in the interface and do not save a half-entered interval.

## Output and persistence

- By default, save the CSV to `ResultsDirectory/Step08_IdentifyRemovals/{ScenarioName}_RemovalSegments.csv`, creating the folder as needed. The Browse CSV control may select another `.csv` path, and Save writes to the currently displayed destination. Saving remains explicit and replaces only that selected CSV while preserving unrelated files.
- Write a header followed by one row per range, with exactly these columns in this order: `ID,StartTime,EndTime,Comment`.
- Format both times as `HH:MM:SS.mmm` from the original video's start. Quote and escape comments according to standard CSV rules so commas, quotes, and newlines are preserved as a single comment field.
- Sort rows by start time and use sequential integer IDs. An empty range list may be saved as a header-only CSV to represent that no sections were marked.
- Write safely so a failed save does not leave a truncated replacement. Do not produce modified video files or other media outputs.

## Run

From the repository root, with the `VideoStitcher` Conda environment active:

Install the Step 08 playback dependency in that environment if it is not already present:

```powershell
conda install -n VideoStitcher --override-channels -c conda-forge opencv=4.13.0 pyside6 qt6-multimedia
```

The explicit OpenCV version replaces the environment's earlier wildcard build constraint during dependency resolution. Qt Multimedia is provided by `qt6-multimedia`; a separate `qt6-multimedia-ffmpeg` package is not required from conda-forge.

Then run Step 08:

```powershell
python Workflow/Step08_IdentifyRemovals.py
```

Open a specific video instead of the configured Step 07 output:

```powershell
python Workflow/Step08_IdentifyRemovals.py --video "path/to/video.mp4"
```

Keep the user-facing entry point under `Workflow/` and reusable implementation under `Utilities/`, following the shared workflow conventions.

## Validation and failure handling

- Validate video readability, dimensions, frame rate, and audio-stream availability with FFprobe before opening it in Qt Multimedia. Use Qt's loaded duration and playback position as authoritative for timeline bounds, seeking, and saved marks. Keep controls disabled until media is ready; report Qt media errors clearly.
- CSV browsing requires a video to be loaded so imported intervals can be checked against its duration. When switching videos, ask whether to save or discard any loaded ranges before clearing them; after confirmation, restore the configured scenario’s default CSV destination.
- Reject a selected existing CSV with a malformed header or row, invalid time, out-of-duration interval, or overlap. Keep the prior range list and output destination unchanged when import fails.
- Validate each range on creation and edit. Reject missing endpoints, non-finite or out-of-duration timestamps, zero-length or reversed ranges, and overlaps; preserve the last valid range when an edit fails.
- Keep GUI seeking responsive for long videos and report decode or seek failures clearly. Show the destination path and error on CSV write failure; preserve any prior valid CSV if saving fails.
- Close cleanly without changing the source video or saving unsaved changes silently.

## Acceptance criteria

- The GUI opens the configured Step 07 stitched video by default, `--video PATH` overrides that startup input, and the user can browse to another video in the GUI.
- The user can play, pause, stop, and scrub video and audio through the same Qt player, and mark interval endpoints using the player's media position. Videos without audio remain usable with a clear status.
- The user can add a comment, edit or remove any interval, and see ranges sorted by start time with sequential IDs. Invalid and overlapping ranges are rejected with an explanation.
- The user can browse to a `.csv` destination. Existing valid CSV rows load for editing, while invalid files are rejected without changing current state. Saving writes the specified four-column CSV with a header, `HH:MM:SS.mmm` timestamps, escaped comments, and the selected destination (the scenario-based path by default).
- Save failures preserve a prior valid CSV. The source video remains unchanged, and no video output is created.
