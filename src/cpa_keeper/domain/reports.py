"""Provider-neutral aggregate reports for one run phase."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .identifiers import validate_stable_identifier


class RunPhase(StrEnum):
    """A maintenance phase included in aggregate reports."""

    FAST_SCAN = "fast_scan"
    INSPECTION = "inspection"


@dataclass(frozen=True, slots=True)
class ProviderRunReport:
    """Aggregate outcome counts for one provider and phase."""

    provider_id: str
    phase: RunPhase
    scanned: int = 0
    matched: int = 0
    planned: int = 0
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    unsupported: int = 0

    def __post_init__(self) -> None:
        validate_stable_identifier(self.provider_id, "provider_id")
        if not isinstance(self.phase, RunPhase):
            raise ValueError("phase must be a RunPhase")
        for field_name in (
            "scanned",
            "matched",
            "planned",
            "applied",
            "skipped",
            "failed",
            "unsupported",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.matched > self.scanned:
            raise ValueError("matched cannot exceed scanned")
        if self.planned > self.matched:
            raise ValueError("planned cannot exceed matched")
        if self.applied > self.planned:
            raise ValueError("applied cannot exceed planned")

    @property
    def total_outcomes(self) -> int:
        """Count terminal run outcomes, excluding matched/planned bookkeeping."""
        return self.applied + self.skipped + self.failed + self.unsupported
