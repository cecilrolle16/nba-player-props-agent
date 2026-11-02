"""Run the synthetic offline demo from the repository root.

This launcher does not read .env. The implementation lives in pipeline.demo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pipeline.demo import main

if __name__ == "__main__":
    raise SystemExit(main())
