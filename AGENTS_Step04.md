# Step 04: Select Image Coordinates

## Purpose

Provide a small desktop GUI for selecting one point in each of two images. Record each point in pixel coordinates measured from that image's top-left corner. This step is independently callable and consumes image files without changing them.

## Inputs

- Let the user browse independently for the left image and the right image. Do not require fixed filenames or a preselected Step 03 timestamp pair.
- Support common raster image formats available through the chosen image-loading library, including JPEG, PNG, and BMP. The file picker should use suitable image filters while allowing the user to select all files if needed.
- Use the installed OpenCV Python bindings (`cv2`) to decode and scale the supported images.
- Show the selected file paths in the GUI. A selection on one side must not change the file selected on the other side.
- Report a clear, side-specific error when a path is missing, the image cannot be decoded, or its dimensions cannot be determined. Keep the current valid selection intact if a replacement selection fails.

## GUI behavior

- Implement the user-facing entry point as `Workflow/Step04_SelectImageCoordinates.py` and reusable behavior under `Utilities/`, following the repository's Python workflow conventions. The entry point must run from the repository root with `python Workflow/Step04_SelectImageCoordinates.py`.
- Use Tkinter, consistent with the existing alignment application.
- Display the left and right images side by side, each with its own Browse control and click target. Preserve each image's aspect ratio when scaling it to fit the available display area; do not alter the source file.
- Accept a click only inside the displayed image area. Convert the click to coordinates in the original image's pixel grid, with `(0, 0)` at the top-left and `x` increasing rightward and `y` increasing downward. Account for any letterboxing or padding around a scaled image so padding clicks are ignored.
- Display the selected pixel coordinates for each side as an ordered list, and show a visible marker for every selected point. Each valid click adds a point to that image's list; do not replace earlier points. Let the user select a listed point and remove it or modify its location, updating both its coordinate and marker. Selecting a different image clears that side's points because they refer to the prior image.
- Permit either side to be selected and clicked first. Clearly indicate when a side has no image or no point selected.

## Outputs and persistence

- Save coordinate records as JSON in `ResultsDirectory/Step04_ImageCoordinates/`, resolving a relative `ResultsDirectory` from the repository root as other workflow steps do. Read the repository-root `settings.json` for the configured results directory; if it cannot be read or resolved, show an actionable error.
- Include each selected image path, original image width and height, and its ordered list of integer `{ "x": ..., "y": ... }` coordinates. Include a unique timestamp in the filename to avoid overwriting unrelated records; because inputs are user-selected files, do not assume their names follow Step 03 conventions.
- Provide an explicit save action. Do not save incomplete records silently. If either image is missing or has no points, explain what is missing and do not write a partial record.
- Create the output folder as needed. On write failure, report the destination and error without modifying the source images.
- Provide an export action that accepts an existing coordinate JSON record and writes a `.txt` file. For each image, write its `ImagePath` on a line followed by that image's points in JSON order, one per line as `[x, y],`. Write the left image group first, then the right image group, with a blank line between them. Let the user choose the text destination and do not overwrite the input JSON.

## Validation and failure handling

- Validate each image before replacing the displayed selection: it must exist, decode successfully, and have positive dimensions.
- Ensure clicks outside the actual displayed image (including padding) are ignored. Clamp valid edge clicks to the nearest valid pixel so `0 <= x < width` and `0 <= y < height`.
- Keep the GUI responsive during ordinary image loading and saving. Show errors in the interface and retain the last valid display when a replacement file is invalid.
- Close the application cleanly without changing input files.

## Acceptance criteria

- The user can browse independently for left and right images, see both images at once, and select either one first.
- Clicking within a displayed image reports the corresponding original-image pixel coordinates, including when the image is scaled. Padding clicks do not create points; valid boundary clicks produce in-range coordinates.
- Every valid click adds a marker and coordinate to the corresponding side's ordered list without removing earlier points. Replacing one side's image clears only that side's points.
- The user can select and remove any listed point or modify its location; the coordinate list and displayed marker stay synchronized after edits.
- Saving a complete pair writes a valid JSON record under `ResultsDirectory/Step04_ImageCoordinates/` with both paths, dimensions, and ordered coordinate lists. Missing inputs, empty point lists, failed image reads, and failed writes produce actionable errors.
- Exporting a valid coordinate JSON writes both image paths and their corresponding ordered point lists as `[x, y],` lines in a text file; malformed JSON or failed writes produce an actionable error.
- The workflow can be started from the repository root with `python Workflow/Step04_SelectImageCoordinates.py`; reusable logic resides under `Utilities/`.
