# Step 07b: Convert Video Resolution and Frame Rate

## Purpose

Use the reusable `videoAlignment.video_conversion.convert_video` function to create a lower-resolution, lower-frame-rate copy of a video. The user-facing entry point is `Workflow/Step07a_CreateProxyVideo.py`; it calls the reusable function and does not modify the source.

## Inputs and behavior

- Pass the original video path and a distinct output video path. The output file extension selects the container format.
- Optional parameters are `width`, `height`, and `fps`; they default to 640, 360, and 10 frames per second.
- Preserve the source aspect ratio and pad the image to the requested dimensions. Width and height must be positive, even integers for H.264 `yuv420p` output. The frame rate must be positive and finite and may be fractional.
- Encode video using H.264, `medium` preset, CRF 18, and `yuv420p`. Copy source audio streams without re-encoding when present. Silent inputs produce silent outputs.
- The verified output duration must be within one target-frame interval of the original. Existing output files are replaced only after successful conversion and verification. Input and output paths must differ.
- The function returns the resolved output path and raises `VideoConversionError` with an actionable message if validation, conversion, or output verification fails.

## Launcher

- Create `Workflow/Step07a_CreateProxyVideo.py` as the runnable launcher and keep the reusable conversion implementation in `Utilities/videoAlignment/video_conversion.py`.
- Accept required `--input PATH` and `--output PATH` arguments, plus optional `--width`, `--height`, and `--fps` arguments. Use the utility defaults when the optional arguments are omitted. Resolve relative paths from the repository root.
- Print the resulting output path on success. On conversion failure, print an actionable error and return a non-zero exit code.

Run from the repository root with the `VideoStitcher` Conda environment active:

```powershell
python Workflow/Step07a_CreateProxyVideo.py --input "Inputs/game_recording.mp4" --output "Outputs/game_recording_720p_15fps.mp4" --width 1280 --height 720 --fps 15
```

## Example

From the repository root with the `VideoStitcher` Conda environment active, call the utility with non-default dimensions and frame rate:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "Utilities"))
from videoAlignment.video_conversion import convert_video

output_path = convert_video(
    input_path=Path("Inputs/game_recording.mp4"),
    output_path=Path("Outputs/game_recording_720p_15fps.mp4"),
    width=1280,
    height=720,
    fps=15,
)
print(f"Converted video saved to: {output_path}")
```

This direct function example is also available to other Python code. Use the launcher above for command-line conversion.

## Acceptance criteria

- Calling `convert_video` with only input and output paths uses 640×360 at 10 fps; caller-supplied dimensions and frame rate override these defaults.
- Output dimensions and frame rate match the requested values, aspect ratio is preserved with padding, and audio is present exactly when the source contains audio.
- Output duration differs from the source by no more than one output frame. The original remains unchanged, and failed conversions do not replace an existing destination.
- `Workflow/Step07a_CreateProxyVideo.py` can be run from the repository root with required input/output paths and optional conversion parameters; it invokes the reusable function and reports success or failure clearly.
- The function can be imported and used independently of Step 08 or other workflow steps.
