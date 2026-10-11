"""Command-line entry point for the RECURSION 94 art compiler."""

from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.asset_compiler.compiler import main
else:
    from .compiler import main


if __name__ == "__main__":
    raise SystemExit(main())
