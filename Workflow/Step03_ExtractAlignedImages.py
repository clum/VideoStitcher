"""Run Step 03 image extraction from the repository root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Utilities"))

from videoAlignment.step03_extract_images import main


if __name__ == "__main__":
    raise SystemExit(main(ROOT))
