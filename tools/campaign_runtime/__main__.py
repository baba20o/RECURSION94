"""Command-line entry point for ``python -m tools.campaign_runtime``."""

from .compiler import main


if __name__ == "__main__":
    raise SystemExit(main())
