"""Configuration models for provider-scoped fast-scan rules."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class ConfigError(ValueError):
    """Raised when the fast-scan configuration cannot be used."""


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class RuleCondition(BaseModel):
    """One recursive fast-scan rule condition."""

    model_config = ConfigDict(extra="forbid")

    all: list["RuleCondition"] | None = None
    any: list["RuleCondition"] | None = None
    field: Literal["error.type", "error.code", "error.message"] | None = None
    op: Literal["eq", "contains"] | None = None
    value: str | None = None
    ignore_case: bool = False

    @model_validator(mode="after")
    def validate_shape(self) -> "RuleCondition":
        groups = (self.all is not None) + (self.any is not None) + (self.field is not None)
        if groups != 1:
            raise ValueError("condition needs exactly one of all, any, or field")
        if self.all is not None or self.any is not None:
            children = self.all if self.all is not None else self.any
            if not children:
                raise ValueError("all and any need at least one condition")
            if self.op is not None or self.value is not None or self.ignore_case:
                raise ValueError("group conditions only accept all or any")
        elif self.op is None or self.value is None or not self.value.strip():
            raise ValueError("leaf conditions require a non-empty op and value")
        return self


RuleCondition.model_rebuild()


class FastScanRule(BaseModel):
    """A configured disable or delete action."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_IDENTIFIER.pattern)
    enabled: bool = True
    action: Literal["disable", "delete"]
    priority: int
    when: RuleCondition


class ProviderFastScanConfig(BaseModel):
    """Rules and switch for one provider's fast scan."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    rules: list[FastScanRule] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def unique_rule_ids(cls, rules: list[FastScanRule]) -> list[FastScanRule]:
        if len({rule.id for rule in rules}) != len(rules):
            raise ValueError("rule IDs must be unique per provider")
        return rules


class InspectionConfig(BaseModel):
    """Existing provider inspection runtime parameters."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    interval_seconds: int = Field(gt=0)
    workers: int = Field(gt=0)
    usage_timeout_seconds: int = Field(gt=0)
    quota_threshold_percent: int = Field(ge=0, le=100)
    refresh_enabled: bool = True
    refresh_before_expiry_days: int = Field(ge=0)


class ProviderConfig(BaseModel):
    """Provider switch plus optional fast-scan and inspection settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    fast_scan: ProviderFastScanConfig = Field(
        default_factory=lambda: ProviderFastScanConfig(enabled=False)
    )
    inspection: InspectionConfig | None = None


class GlobalFastScanConfig(BaseModel):
    """The shared CPA list-read cadence for every provider."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(gt=0)


class ControlPlaneConfig(BaseModel):
    """Optional shared HTTP settings kept outside fast-scan rules."""

    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(default=30, gt=0)
    max_retries: int = Field(default=2, ge=0)


class RuntimeConfig(BaseModel):
    """Runtime behavior loaded from TOML without connection secrets."""

    model_config = ConfigDict(extra="forbid")

    control_plane: ControlPlaneConfig = Field(default_factory=ControlPlaneConfig)
    fast_scan: GlobalFastScanConfig
    providers: dict[str, ProviderConfig] = Field(min_length=1)

    @field_validator("providers")
    @classmethod
    def safe_provider_ids(cls, providers: dict[str, ProviderConfig]) -> dict[str, ProviderConfig]:
        if not all(_IDENTIFIER.fullmatch(provider_id) for provider_id in providers):
            raise ValueError("provider IDs use lowercase letters, digits, dots, underscores, and hyphens")
        return providers

    @model_validator(mode="after")
    def codex_owns_inspection(self) -> "RuntimeConfig":
        if any(
            provider_id != "codex" and provider.inspection is not None
            for provider_id, provider in self.providers.items()
        ):
            raise ValueError("inspection is only supported for codex")
        return self


def parse_config_data(data: Mapping[str, object]) -> RuntimeConfig:
    """Validate TOML data and expose the small runtime configuration model."""
    try:
        return RuntimeConfig.model_validate(data)
    except ValidationError as exc:
        messages = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigError(messages) from exc
