"""Create a lower-resolution, lower-frame-rate video copy using Step 07b."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Utilities"))

from videoAlignment.video_conversion import VideoConversionError, convert_video


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a lower-resolution, lower-frame-rate copy of a video."
    )
    parser.add_argument("--input", required=True, help="Path to the source video.")
    parser.add_argument("--output", required=True, help="Destination video path; its extension selects the container.")
    parser.add_argument("--width", type=int, default=640, help="Output width in pixels (default: 640).")
    parser.add_argument("--height", type=int, default=360, help="Output height in pixels (default: 360).")
    parser.add_argument("--fps", type=float, default=10, help="Output frame rate (default: 10 fps).")
    args = parser.parse_args(argv)

    try:
        output_path = convert_video(
            input_path=_resolve_path(args.input),
            output_path=_resolve_path(args.output),
            width=args.width,
            height=args.height,
            fps=args.fps,
        )
    except (VideoConversionError, OSError, ValueError) as exc:
        print(f"Step 07b failed: {exc}", file=sys.stderr)
        return 1

    print(f"Converted video saved to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
