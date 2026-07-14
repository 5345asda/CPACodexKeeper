"""Run the v1 command line interface with ``python -m cpa_keeper``."""

from .cli.commands import main


if __name__ == "__main__":
    raise SystemExit(main())
