"""Safe metadata contracts for CPA auth-file list rows."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .identifiers import validate_stable_identifier


_SAFE_SUMMARY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_FORBIDDEN_SUMMARY_FRAGMENTS = frozenset(
    {
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
    }
)


def validate_safe_summary(value: object, field_name: str = "safe_summary") -> str:
    """Accept only semantic identifiers, never raw upstream response content."""
    if not isinstance(value, str) or len(value) > 160:
        raise ValueError(f"{field_name} must be a short semantic identifier")
    normalized = value.lower()
    if not _SAFE_SUMMARY_PATTERN.fullmatch(value) or any(
        fragment in normalized for fragment in _FORBIDDEN_SUMMARY_FRAGMENTS
    ):
        raise ValueError(f"{field_name} must not contain raw credential or response material")
    return value


@dataclass(frozen=True, slots=True)
class AuthFileMetadata:
    """Metadata used to evaluate policies without carrying auth-file contents."""

    name: str
    provider_id: str
    disabled: bool
    status: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty auth-file identifier")
        validate_stable_identifier(self.provider_id, "provider_id")
        if type(self.disabled) is not bool:
            raise ValueError("disabled must be a boolean")
        validate_stable_identifier(self.status, "status")
