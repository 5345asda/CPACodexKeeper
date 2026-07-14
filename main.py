"""Retired v0 entrypoint retained only to prevent accidental dual-daemon writes."""

from __future__ import annotations

import sys


def main() -> int:
    """Refuse the legacy runtime and direct operators to the v1 CLI."""
    print(
        "CPACodexKeeper v0 main.py is retired. "
        "Use `cpa-keeper --help` and docs/reference/config.example.toml.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
