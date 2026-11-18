"""Run Step 09b ROI cropping from the repository root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Utilities"))

from videoAlignment.step09b_crop_to_roi import main


if __name__ == "__main__":
    raise SystemExit(main(ROOT))
