# Step 01: Combine Raw Camera Videos

## Purpose

Combine each camera's raw video segments into one file so Step 02 can align the two complete camera recordings. This step stream-copies compatible video and audio streams and does not modify its source clips.

## Inputs

- By default, read the repository-root `settings.json`. Accept `--settings PATH` to use another settings file; resolve a relative settings path from the repository root.
- Use `ScenarioName`, `ResultsDirectory`, `CameraA.RawVideoFilePath`, and `CameraB.RawVideoFilePath` from the selected settings file.
- Each `RawVideoFilePath` must point to an existing folder. Relative input and `ResultsDirectory` paths resolve from the repository root, even when a separate settings file is selected.
- Include supported video files directly inside each camera folder: `.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v`, `.webm`, `.mts`, and `.m2ts`. Do not scan subfolders.
- Combine each camera's files in modification-time order, oldest first. Use natural filename order to break modification-time ties.
- All clips for a camera must have the same extension and compatible audio/video stream layouts and codec settings for stream-copy concatenation. Ancillary data streams are omitted.

## Processing

- Print the full ordered input-file list for each camera before combining it.
- Use FFmpeg's concat demuxer with stream copy for video and audio. Preserve the common source extension for each camera's combined output.
- Display a determinate console progress bar for the complete two-camera operation, based on the input durations.
- Stage and validate both camera outputs before publishing either one. On publication failure, restore the prior pair where possible.

## Output

- Write `{ScenarioName}_CameraA{extension}` and `{ScenarioName}_CameraB{extension}` under `ResultsDirectory/Step01_CombinedVideos/`, using the sanitized scenario name convention.
- Create the output folder if needed. A rerun replaces only this fixed pair; preserve source clips and unrelated files, including numbered legacy variants.

## Run

Open a terminal at the repository root and activate the `VideoStitcher` Conda environment:

```powershell
conda activate VideoStitcher
```

Then run Step 01:

```powershell
python Workflow/Step01_CombineRawVideos.py
```

Use a user-selected settings file:

```powershell
python Workflow/Step01_CombineRawVideos.py --settings "path/to/settings.json"
```

Absolute paths are also accepted. Relative `--settings` paths resolve from the repository root.

FFmpeg and ffprobe must be installed and available on `PATH` in that environment.

## Validation and failure handling

- Report actionable errors for missing or invalid settings, camera folders, absent supported inputs, multiple extensions within a camera folder, incompatible stream signatures, unavailable FFmpeg tools, unreadable media, and output staging or publishing failures.
- Probe every input duration and validate the combined outputs before publishing. Do not replace the prior output pair unless both new outputs have been created and validated.
- Keep all source files unchanged and preserve unrelated outputs on success and failure.

## Acceptance criteria

- The configured Camera A and Camera B folders are combined independently, each in deterministic modification-time/natural-name order.
- The output pair appears in `ResultsDirectory/Step01_CombinedVideos/` with scenario-based names and each camera's source extension.
- Progress covers both camera operations, compatible streams are copied without re-encoding, and ancillary data streams are excluded.
- Failed runs do not publish a partial pair or alter source files; successful reruns replace only the fixed pair.
