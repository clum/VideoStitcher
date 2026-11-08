"""Launch the Step 04 image coordinate selection GUI."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Utilities"))

from videoAlignment.step04_select_image_coordinates import main


if __name__ == "__main__":
    raise SystemExit(main(ROOT))
