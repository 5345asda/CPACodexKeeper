"""Load fast-scan TOML behavior and CPA connection secrets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib

from dotenv import dotenv_values

from .fast_scan import ConfigError, RuntimeConfig, parse_config_data


class ConfigLoadError(ValueError):
    """Raised when runtime configuration or required secrets are missing."""


@dataclass(frozen=True, slots=True)
class ConnectionConfig:
    """Connection values kept outside TOML behavior rules."""

    endpoint: str = field(repr=False)
    token: str = field(repr=False)
    proxy: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class LoadedRuntimeConfig:
    """Validated behavior plus the connection settings used to build clients."""

    behavior: RuntimeConfig
    connection: ConnectionConfig = field(repr=False)
    config_path: Path
    env_file_path: Path | None


def read_env_file(path: Path) -> dict[str, str]:
    """Read dotenv values through python-dotenv without shell evaluation."""
    return {
        key: value
        for key, value in dotenv_values(path).items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _secret(name: str, process_environment: Mapping[str, str], env_values: Mapping[str, str]) -> str | None:
    if name in process_environment:
        return process_environment[name]
    return env_values.get(name)


def load_runtime_config(
    *,
    config_path: Path | None = None,
    env_file: Path | None = None,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> LoadedRuntimeConfig:
    """Load target TOML behavior and resolve environment values over dotenv values."""
    working_directory = (cwd or Path.cwd()).resolve()
    chosen_config = (config_path or working_directory / "config.toml").resolve()
    chosen_env = (env_file or working_directory / ".env").resolve()
    if not chosen_config.is_file():
        raise ConfigLoadError(f"config file is required: {chosen_config}")
    try:
        with chosen_config.open("rb") as config_file:
            behavior = parse_config_data(tomllib.load(config_file))
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as exc:
        raise ConfigLoadError(str(exc)) from exc

    process_environment = os.environ if environ is None else environ
    env_values = read_env_file(chosen_env)
    endpoint = _secret("CPA_ENDPOINT", process_environment, env_values)
    token = _secret("CPA_TOKEN", process_environment, env_values)
    if not endpoint or not token:
        raise ConfigLoadError("CPA_ENDPOINT and CPA_TOKEN are required")
    proxy = _secret("CPA_PROXY", process_environment, env_values) or None
    return LoadedRuntimeConfig(
        behavior=behavior,
        connection=ConnectionConfig(
            endpoint=endpoint,
            token=token,
            proxy=proxy,
        ),
        config_path=chosen_config,
        env_file_path=chosen_env if chosen_env.exists() else None,
    )
