"""Metadata for CPA auth-file list rows, without credential contents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthFileMetadata:
    name: str
    provider_id: str
    disabled: bool
    status: str
