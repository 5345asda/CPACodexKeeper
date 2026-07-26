"""Log-safe resource identification."""

from __future__ import annotations

from hashlib import sha256


def resource_hash(name: str) -> str:
    """Short stable hash so logs can correlate a resource without naming it."""
    return sha256(name.encode()).hexdigest()[:12]
