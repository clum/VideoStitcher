# Video Stitcher

## Purpose

Build a Python desktop application that helps a user manually align two recordings of the same event, filmed from different angles. The primary result is the signed time offset to apply to the second video so its content aligns with the first. The app also determines the matching start and end trims needed to make both aligned clips exactly the same duration.

## User workflow

1. Let the user choose two video files. Treat the first as the reference and the second as the clip being aligned. Do not modify source files.
2. Read each file's duration, frame rate or timestamp information, dimensions, and audio stream availability. Show clear errors for unreadable files and warn when either clip has no audio.
3. Display both videos and their audio tracks in a common timeline. Provide playback controls and allow the user to drag the second clip earlier or later relative to the first. Moving the second clip must move its video and audio together. Keep the reference clip fixed.
4. Let the user play or scrub both clips while they are aligned on the timeline, so visual events and sound can be compared. Show the current offset with sufficient precision and make the drag interaction usable for fine adjustments.
5. Once the user accepts the alignment, calculate the overlapping interval. Report the time offset and the start/end crop amounts for both inputs. Both resulting clips must cover the same event interval and have exactly the same duration.
6. Present the result clearly, with a copyable offset in seconds and a human-readable time format. Keep the alignment result available independently of any later export feature.

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
- Include an example Python script under `Test/` that invokes the main application entry point, so users and developers have a clear pattern for starting the app.
- Encapsulate application behavior in callable Python scripts, functions, classes, and/or libraries. Store this code under `Utilities/` in an organized package structure, with the user interface calling reusable functionality where practical.
- Define a stable main application entry point that can be called from both the example launcher and normal application startup.
- Preserve source media. Any future export must write new files and must use the calculated common interval for both clips.
- Use a media playback approach that supports both video and audio and handles differing frame rates, stream timestamps, and audio sample rates. Keep playback position and offset calculations based on media timestamps rather than assuming both videos have identical frame rates.
- Use precise internal time values (for example, integer stream time-base ticks or rational values where practical). Round only for display or when an export format requires frame boundaries. Explain any frame-boundary rounding in the result.
- Keep media probing, offset/overlap calculations, and UI behavior in separable modules so the calculations can be maintained independently of the interface.
- Handle paths with spaces and Unicode, long clips, seeking, paused playback, and missing audio without silently shifting the requested offset.
- Do not infer or automatically change the user's chosen alignment. The user controls the second clip's position; any automatic assistance must remain optional and must not replace the displayed manual offset.

## Completion criteria

- The user can load two videos and compare their video and audio during playback.
- Dragging the second video changes its signed offset while keeping its audio attached.
- The app displays the exact signed offset and derives the shared interval and both clips' beginning and ending crop amounts consistently.
- The resulting clips' requested time span is equal, and the app reports when the selected offset leaves no shared interval.
- Original files remain untouched.
