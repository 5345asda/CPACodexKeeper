"""Secret-safe phase results and command-status classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from cpa_keeper.domain.auth_files import AuthFileMetadata
from cpa_keeper.domain.reports import ProviderRunReport


class RunStatus(StrEnum):
    """Stable command outcomes that distinguish empty from upstream failure."""

    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL_FAILURE = "partial_failure"
    UPSTREAM_FAILURE = "upstream_failure"


def _freeze_reports(reports: tuple[ProviderRunReport, ...]) -> tuple[ProviderRunReport, ...]:
    report_tuple = tuple(reports)
    if not all(isinstance(report, ProviderRunReport) for report in report_tuple):
        raise ValueError("reports must contain ProviderRunReport values")
    return report_tuple


@dataclass(frozen=True, slots=True)
class FastScanResult:
    """Fast-scan outcome; auth-file names are hidden from repr."""

    status: RunStatus
    reports: tuple[ProviderRunReport, ...]
    metadata: tuple[AuthFileMetadata, ...] = field(default_factory=tuple, repr=False)
    handled_resource_names: frozenset[str] = field(default_factory=frozenset, repr=False)
    resource_epochs: Mapping[str, int] = field(default_factory=dict, repr=False)
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, RunStatus):
            raise ValueError("status must be a RunStatus")
        object.__setattr__(self, "reports", _freeze_reports(self.reports))
        metadata = tuple(self.metadata)
        if not all(isinstance(item, AuthFileMetadata) for item in metadata):
            raise ValueError("metadata must contain AuthFileMetadata values")
        object.__setattr__(self, "metadata", metadata)
        handled_names = frozenset(self.handled_resource_names)
        if not all(isinstance(name, str) and name.strip() for name in handled_names):
            raise ValueError("handled_resource_names must contain non-empty strings")
        object.__setattr__(self, "handled_resource_names", handled_names)
        resource_epochs = dict(self.resource_epochs)
        if not all(
            isinstance(name, str) and name.strip() and type(epoch) is int and epoch >= 0
            for name, epoch in resource_epochs.items()
        ):
            raise ValueError("resource_epochs must map auth-file names to non-negative integers")
        object.__setattr__(self, "resource_epochs", MappingProxyType(resource_epochs))
        if self.error_code is not None and (
            not isinstance(self.error_code, str) or not self.error_code
        ):
            raise ValueError("error_code must be a non-empty string when set")
        if self.status is RunStatus.UPSTREAM_FAILURE and self.error_code is None:
            raise ValueError("upstream_failure requires an error_code")


@dataclass(frozen=True, slots=True)
class InspectionResult:
    """Inspection phase outcome containing only aggregate reports."""

    status: RunStatus
    reports: tuple[ProviderRunReport, ...]
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, RunStatus):
            raise ValueError("status must be a RunStatus")
        object.__setattr__(self, "reports", _freeze_reports(self.reports))
        if self.error_code is not None and (
            not isinstance(self.error_code, str) or not self.error_code
        ):
            raise ValueError("error_code must be a non-empty string when set")


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """Combined fast-scan and inspection outcome for one manual maintenance round."""

    status: RunStatus
    reports: tuple[ProviderRunReport, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, RunStatus):
            raise ValueError("status must be a RunStatus")
        object.__setattr__(self, "reports", _freeze_reports(self.reports))
