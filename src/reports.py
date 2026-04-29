import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TokenReport:
    name: str
    email: str | None = None
    disabled: bool = False
    expiry: str | None = None
    expiry_remaining_seconds: int | None = None
    plan_type: str | None = None
    primary_used_percent: int | None = None
    secondary_used_percent: int | None = None
    primary_window_seconds: int | None = None
    secondary_window_seconds: int | None = None
    has_credits: bool | None = None
    last_outcome: str | None = None
    last_actions: list[str] = field(default_factory=list)
    last_log_lines: list[str] = field(default_factory=list)
    checked_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "email": self.email,
            "disabled": self.disabled,
            "expiry": self.expiry,
            "expiry_remaining_seconds": self.expiry_remaining_seconds,
            "plan_type": self.plan_type,
            "primary_used_percent": self.primary_used_percent,
            "secondary_used_percent": self.secondary_used_percent,
            "primary_window_seconds": self.primary_window_seconds,
            "secondary_window_seconds": self.secondary_window_seconds,
            "has_credits": self.has_credits,
            "last_outcome": self.last_outcome,
            "last_actions": list(self.last_actions),
            "last_log_lines": list(self.last_log_lines),
            "checked_at": self.checked_at,
        }


class ReportRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reports: dict[str, TokenReport] = {}

    def upsert(self, report: TokenReport) -> None:
        with self._lock:
            self._reports[report.name] = report

    def remove(self, name: str) -> None:
        with self._lock:
            self._reports.pop(name, None)

    def get(self, name: str) -> TokenReport | None:
        with self._lock:
            return self._reports.get(name)

    def all(self) -> list[TokenReport]:
        with self._lock:
            return list(self._reports.values())

    def replace_all(self, reports: list[TokenReport]) -> None:
        with self._lock:
            self._reports = {report.name: report for report in reports}

    def touch(self, name: str) -> TokenReport:
        """Get or create a TokenReport for the given name, with checked_at stamped."""
        with self._lock:
            report = self._reports.get(name)
            if report is None:
                report = TokenReport(name=name)
                self._reports[name] = report
            report.checked_at = time.time()
            report.last_actions = []
            report.last_log_lines = []
            return report
