# Step 07: Stitch Aligned Videos

## Purpose

Apply the Step 05 homography to synchronized frames from the aligned Camera A and Camera B videos, then write one panorama video. Follow Step 06's transform, panorama-canvas, and overlap-compositing behavior for every frame.

## Inputs

- By default, read the repository-root `settings.json`. Accept `--settings PATH` to use another settings JSON; relative paths resolve from the repository root. Use the selected file's `ScenarioName` and `ResultsDirectory` for default video, homography, and output paths. Relative `ResultsDirectory` paths resolve from the repository root.
- By default, select the configured scenario's canonical `CameraA_Aligned` and `CameraB_Aligned` MP4 files in `ResultsDirectory/Step02_AlignedVideos/`. Do not choose inputs solely by modification time: derived clips may be newer than the aligned source videos.
- Accept `--videos LEFT RIGHT` to explicitly select the left/Camera A and right/Camera B inputs. Resolve relative paths from the repository root.
- By default, load `ResultsDirectory/Step05_Homography/{ScenarioName}_Homography.json`. Accept `--homography PATH` to select another artifact; resolve a relative path from the repository root.
- Validate that both video streams can be opened, have usable timestamps and positive frame rates/dimensions, and that the homography artifact is valid and invertible. Follow its `CoordinateRecordPath` to the exact Step 04 record and use that record for the matched landmarks and the Step 06 horizon-crossing similarity fallback.
- Ensure the videos correspond to the homography's image pair and that their frame dimensions match the coordinate record's source image dimensions. If filenames differ because explicit videos were selected, dimensions and scenario/provenance still need to be checked and any mismatch explained clearly.

## Processing

- The homography maps right-camera pixels into the left-camera coordinate system. Use the same full transformed-right-image panorama canvas and horizon-crossing similarity fallback as Step 06. Do not crop the output to the left frame.
- Synchronize frames by presentation timestamps, not by assuming frame number equality or equal frame rates. Generate output on Camera A's timeline at Camera A's frame rate, pair each output time with the corresponding Camera B frame by timestamp, and stop at the shorter usable video duration. Handle timestamp gaps or decode failures explicitly rather than silently pairing unrelated frames.
- Composite each frame using the Step 06 rule: left-camera pixels take priority wherever covered; fill uncovered canvas pixels with warped Camera B pixels. Accept `--overlap-mode blend` for equal-weight blending in overlap regions; default to `left`. The selected mode must not change the standalone warped-right geometry.
- Show useful progress, such as processed frames and elapsed or estimated remaining time when duration is known. Report the selected inputs, transform, overlap mode, output frame rate, and whether Camera A audio is present.

## Output and encoding

- Write `{ScenarioName}_Stitched.mp4` under `ResultsDirectory/Step07_StitchedVideos/`.
- Use Camera A's frame rate and audio. If Camera A has no audio stream, produce a silent MP4 and report this clearly. Do not take audio from Camera B.
- Follow Step 02's H.264/AAC export settings (H.264 video, AAC audio where present, and the same quality, pixel-format, and audio parameters configured there). Preserve the intended playback duration and timestamps.
- Encode to a temporary file in the Step 07 output folder. Verify the completed file can be probed/read and has expected video dimensions, frame rate, duration, and audio presence before replacing the existing scenario output. On failure, remove only the temporary file and leave any prior output untouched.
- Reruns replace only `{ScenarioName}_Stitched.mp4`; preserve both source videos, homography/coordinate artifacts, and unrelated Step 07 files.

## Run

From the repository root, with the `VideoStitcher` Conda environment active:

```powershell
python Workflow/Step07_StitchVideos.py
```

Select a specific video pair:

```powershell
python Workflow/Step07_StitchVideos.py --videos "path/to/left.mp4" "path/to/right.mp4"
```

Select a specific homography and blend overlap pixels:

```powershell
python Workflow/Step07_StitchVideos.py --homography "Outputs/Step05_Homography/example_Homography.json" --overlap-mode blend
```

Use a user-selected settings file:

```powershell
python Workflow/Step07_StitchVideos.py --settings "settings_26_11_01_Soccer_WssaU14.json"
```

Options can be combined. Explicit video paths are ordered left/Camera A first, then right/Camera B.

## Failure handling

- Give actionable errors for missing settings or default inputs, unreadable videos, absent/invalid timestamps, invalid or mismatched homography and coordinate records, dimension mismatches, unsupported overlap modes, frame decode failures, unavailable encoders, disk/write failures, and failed output verification.
- Do not replace a previous stitched output unless the new file has finished encoding and passed verification. Preserve source videos and unrelated outputs on every failure.
- Identify which input or processing stage failed and include the selected file path when useful.

## Acceptance criteria

- Default selection uses the configured scenario's canonical aligned Camera A/B pair; `--videos` independently overrides that pair. Defaults do not accidentally select newer derived clips.
- The configured scenario homography is used by default, and `--homography` selects an explicit artifact. The artifact's coordinate-record reference supplies the correct landmark pairs for the same fallback behavior as Step 06.
- Every output frame uses the full transformed panorama canvas and is timestamp-aligned. Processing stops at the shorter input duration.
- Left-priority is the default; blend mode changes only overlap composition. Output uses Camera A's frame rate and Camera A audio when present, otherwise it is silent with a clear report.
- A successful run produces a verified `{ScenarioName}_Stitched.mp4` in the Step 07 result folder. Failed runs leave any prior output, source videos, and unrelated files intact.
