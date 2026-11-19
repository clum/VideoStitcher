"""Launch Step 08a removal-interval identification from the repository root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Utilities"))

from videoAlignment.step08a_identify_removals import main


if __name__ == "__main__":
    raise SystemExit(main(ROOT))
