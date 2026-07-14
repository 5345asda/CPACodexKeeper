"""Stable identifiers used in configuration, reports, and safe logs."""

from __future__ import annotations

import re


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


def validate_stable_identifier(value: object, field_name: str) -> str:
    """Accept lowercase identifiers that are safe to report verbatim."""
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase stable identifier")
    return value
