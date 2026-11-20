# Step 09b: Crop Video to Region of Interest

## Purpose

Crop the Step 08b sections-removed video to the rectangular region selected in Step 09a. Keep the source video and ROI record unchanged. The user can encode a short preview first, then encode the full video after checking the crop.

## Inputs

- By default, read the repository-root `settings.json`. Accept `--settings PATH` to use another settings JSON; relative settings paths resolve from the repository root. Use its `ScenarioName` and `ResultsDirectory`; relative result paths resolve from the repository root.
- By default, read `ResultsDirectory/Step08b_RemovedSections/{ScenarioName}_SectionsRemoved.mp4`. Accept `--video PATH` to choose another source video; relative paths resolve from the repository root.
- By default, select `{ScenarioName}_RegionOfInterest.json` from `ResultsDirectory/Step09a_IdentifyRegionOfInterest/`. If that stable file is absent, select the newest valid legacy `{ScenarioName}_RegionOfInterest_*.json` record. Accept `--roi PATH` to choose a specific ROI record; relative paths resolve from the repository root.
- Validate that the ROI record's scenario matches the selected settings, its frame dimensions match the source video's dimensions, its timestamp and corner coordinates are valid, and its inclusive rectangle lies within the source frame. Use the recorded `TopLeft`, `BottomRight`, `Width_px`, and `Height_px`; the crop origin is the top-left `(x, y)` and crop size is `BottomRight - TopLeft + 1` in each dimension.
- Probe the source video before encoding. Require a readable video stream with positive dimensions, duration, and frame rate. Record its video codec/profile, pixel format, frame rate, and audio stream properties for output verification.

## Processing

- Implement reusable processing in `Utilities/videoAlignment/step09b_crop_to_roi.py` and a launcher in `Workflow/Step09b_CropToRegionOfInterest.py`.
- Apply the ROI to every source frame without resizing the crop. The output video dimensions equal the ROI's inclusive width and height, and the output frame rate and timeline match the source.
- Re-encode the cropped video using the source's video codec and compatible profile, pixel format, and quality or bitrate settings. Cropping requires video re-encoding; preserve the source's other encoding properties where the codec and output container support them. If required properties cannot be retained, report that clearly rather than silently choosing unrelated settings.
- Preserve audio and its timing. Stream-copy compatible audio when possible; otherwise encode using the source audio codec and settings supported by the output container. If the source has no audio, create a silent output.
- Support an optional `--preview-seconds N` argument to encode only the first `N` seconds for review. Require a positive finite duration and cap it at the source duration. When omitted, process the full video. Report whether the output is a preview or the full result.
- Display console progress during encoding, including percentage, elapsed time, and estimated remaining time when available.

## Output and persistence

- Save outputs under `ResultsDirectory/Step09b_CropToRegionOfInterest/`, creating the folder as needed.
- Name the full output `{ScenarioName}_CroppedToRegionOfInterest.mp4`.
- Name preview output separately, for example `{ScenarioName}_CroppedToRegionOfInterest_Preview_{duration}s.mp4`, so preview runs cannot replace the full result.
- Encode to a temporary file in the output folder. Verify the output is readable, has the ROI dimensions and source frame rate, retains expected video and audio properties, and has the requested duration (within one source frame). Atomically replace only the corresponding full or preview output after verification. On failure, remove the temporary file and preserve any existing valid output, source video, ROI JSON, and unrelated files.

## Run

Activate the `VideoStitcher` Conda environment and run from the repository root:

```powershell
conda activate VideoStitcher
python Workflow/Step09b_CropToRegionOfInterest.py
```

Use a specific settings file, source video, or ROI record:

```powershell
python Workflow/Step09b_CropToRegionOfInterest.py --settings "settings_26_11_01_Soccer_WssaU14.json" --video "path/to/sections_removed.mp4" --roi "path/to/region_of_interest.json"
```

Encode a five-second preview before processing the full video:

```powershell
python Workflow/Step09b_CropToRegionOfInterest.py --preview-seconds 5
```

Arguments can be combined. Relative `--settings`, `--video`, and `--roi` paths resolve from the repository root. After reviewing the preview, run again without `--preview-seconds` to create the full output.

## Validation and failure handling

- Give actionable errors for missing or invalid settings, source video, or ROI JSON; scenario or dimension mismatches; invalid coordinates; unsupported codecs or containers; encoding failures; and output verification or write failures.
- Do not modify the source video or ROI record. Never replace a previous output until the new file has been completely encoded and verified.
- Reruns replace only the corresponding scenario full output or preview output; preserve unrelated files in the Step 09b output folder.

## Acceptance criteria

- The default inputs come from the configured scenario's Step 08b video and latest valid Step 09a ROI record. `--settings`, `--video`, and `--roi` select explicit inputs.
- The crop uses the ROI's original source-frame pixel coordinates, including both selected endpoints, and the output dimensions match the inclusive rectangle size.
- `--preview-seconds` produces only the beginning of the source video for quick review without overwriting the full output. Omitting it processes the entire video.
- The output preserves source frame rate, audio behavior, and compatible codec settings while encoding the cropped pixels; duration matches the full source or requested preview within one source frame.
- Encoding progress is visible. Invalid inputs or failed verification leave prior valid outputs, source media, ROI JSON, and unrelated files intact.
