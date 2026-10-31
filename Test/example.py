"""Launch the Video Stitcher from the repository root with ``python Test/example.py``."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Utilities"))

from videoAlignment.app import main


if __name__ == "__main__":
    raise SystemExit(main())
