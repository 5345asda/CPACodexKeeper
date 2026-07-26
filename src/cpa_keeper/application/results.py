"""Phase results and command-status classification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from cpa_keeper.domain.auth_files import AuthFileMetadata
from cpa_keeper.domain.reports import ProviderRunReport


class RunStatus(StrEnum):
    """Command outcomes that distinguish empty from upstream failure."""

    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL_FAILURE = "partial_failure"
    UPSTREAM_FAILURE = "upstream_failure"


@dataclass(frozen=True, slots=True)
class FastScanResult:
    """Fast-scan outcome; auth-file names are hidden from repr."""

    status: RunStatus
    reports: tuple[ProviderRunReport, ...]
    metadata: tuple[AuthFileMetadata, ...] = field(default_factory=tuple, repr=False)
    handled_resource_names: frozenset[str] = field(default_factory=frozenset, repr=False)
    resource_epochs: Mapping[str, int] = field(default_factory=dict, repr=False)
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class InspectionResult:
    """Inspection phase outcome containing only aggregate reports."""

    status: RunStatus
    reports: tuple[ProviderRunReport, ...]


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """Combined fast-scan and inspection outcome for one maintenance round."""

    status: RunStatus
    reports: tuple[ProviderRunReport, ...]
