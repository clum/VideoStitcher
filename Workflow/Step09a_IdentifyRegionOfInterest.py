"""Launch the Step 09a region-of-interest selection GUI."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Utilities"))

from videoAlignment.step09a_identify_roi import main


if __name__ == "__main__":
    raise SystemExit(main(ROOT))
