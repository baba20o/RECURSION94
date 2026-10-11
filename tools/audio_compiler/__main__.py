"""Module and project-local portable-Python entry point."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.audio_compiler.compiler import main
else:
    from .compiler import main


if __name__ == "__main__":
    raise SystemExit(main())
