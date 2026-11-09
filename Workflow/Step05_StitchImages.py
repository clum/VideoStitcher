"""Run Step 05 image stitching from the repository root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Utilities"))

from videoAlignment.step05_stitch_images import main


if __name__ == "__main__":
    raise SystemExit(main(ROOT))
