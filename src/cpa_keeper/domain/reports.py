"""Aggregate per-provider outcome counts for one run phase."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RunPhase(StrEnum):
    FAST_SCAN = "fast_scan"
    INSPECTION = "inspection"


@dataclass(frozen=True, slots=True)
class ProviderRunReport:
    provider_id: str
    phase: RunPhase
    scanned: int = 0
    matched: int = 0
    applied: int = 0
    skipped: int = 0
    failed: int = 0
