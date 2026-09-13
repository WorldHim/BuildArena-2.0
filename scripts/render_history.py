"""Render one valid-only build-history JSON; see docs/rendering.md."""
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buildarena.rendering.pipeline import main

if __name__ == "__main__":
    main()
