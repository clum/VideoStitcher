# Video Stitcher Repository Guide

## How to use these instructions

- Read `AGENTS_Step01_Step02.md` when changing the raw-video combine step or the alignment application.
- Read `AGENTS_Step03.md` when implementing the still-image extraction step.
- Read `AGENTS_Step04.md` when implementing the image-coordinate selection GUI.
- Read `AGENTS_Step05.md` when implementing homography estimation.
- Read `AGENTS_Step06.md` when implementing image stitching.
- Read `AGENTS_Step07.md` when implementing video stitching.
- Read `AGENTS_Step07b.md` when converting a video to a lower resolution or frame rate.
- Read `AGENTS_Step08.md` when implementing removal-range identification.
- Read `AGENTS_Step08b.md` when removing video sections identified by Step 08.
- Keep each step's detailed behavior and acceptance criteria in that step's document.

## Shared workflow conventions

- Activate the `VideoStitcher` Conda environment before running project code.
- Keep each workflow step independently callable, with reusable Python implementation under `Utilities/` and user-facing entry points under `Workflow/`.
- Define each step's inputs, outputs, validation, failure handling, and acceptance criteria in its own instructions.
- Pass data between steps through explicit output files and settings. Resolve configured result paths consistently and preserve source media.
- Keep outputs for each step in a dedicated subfolder under `ResultsDirectory`.
