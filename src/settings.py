import json
import os
import tempfile
import threading
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any


DEFAULT_INTERVAL_SECONDS = 1800
DEFAULT_QUOTA_THRESHOLD = 100
DEFAULT_EXPIRY_THRESHOLD_DAYS = 3
DEFAULT_USAGE_TIMEOUT_SECONDS = 15
DEFAULT_CPA_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES = 2
DEFAULT_WORKER_THREADS = 8
DEFAULT_ENABLE_REFRESH = True
DEFAULT_UI_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 8318
PROJECT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_RUNTIME_OVERRIDES_FILE = Path(__file__).resolve().parents[1] / "runtime.json"


# Fields that take effect on the next maintenance round without restart.
POLICY_FIELDS: tuple[str, ...] = (
    "quota_threshold",
    "expiry_threshold_days",
    "enable_refresh",
    "worker_threads",
    "interval_seconds",
    "max_retries",
)

# Fields that are baked into long-lived clients at startup; require restart.
TRANSPORT_FIELDS: tuple[str, ...] = (
    "cpa_endpoint",
    "cpa_token",
    "proxy",
    "cpa_timeout_seconds",
    "usage_timeout_seconds",
    "ui_host",
    "ui_port",
    "ui_token",
)


class SettingsError(ValueError):
    pass


@dataclass(slots=True)
class Settings:
    cpa_endpoint: str
    cpa_token: str
    proxy: str | None = None
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    quota_threshold: int = DEFAULT_QUOTA_THRESHOLD
    expiry_threshold_days: int = DEFAULT_EXPIRY_THRESHOLD_DAYS
    usage_timeout_seconds: int = DEFAULT_USAGE_TIMEOUT_SECONDS
    cpa_timeout_seconds: int = DEFAULT_CPA_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    worker_threads: int = DEFAULT_WORKER_THREADS
    enable_refresh: bool = DEFAULT_ENABLE_REFRESH
    ui_host: str = DEFAULT_UI_HOST
    ui_port: int = DEFAULT_UI_PORT
    ui_token: str | None = None

    def snapshot(self) -> "Settings":
        return replace(self)

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _read_project_env_file(env_file: Path | None = None) -> dict[str, str]:
    target = env_file or PROJECT_ENV_FILE
    if not target.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _get_config_value(name: str, env_values: dict[str, str]) -> str | None:
    env_value = os.getenv(name)
    if env_value not in (None, ""):
        return env_value
    return env_values.get(name)


def _read_int(name: str, default: int, env_values: dict[str, str], *, minimum: int = 0, maximum: int | None = None) -> int:
    raw = _get_config_value(name, env_values)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if value < minimum:
        raise SettingsError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise SettingsError(f"{name} must be <= {maximum}")
    return value


def _read_bool(name: str, default: bool, env_values: dict[str, str]) -> bool:
    raw = _get_config_value(name, env_values)
    if raw in (None, ""):
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be a boolean")


def _resolve_runtime_overrides_path(env_values: dict[str, str]) -> Path:
    raw = _get_config_value("CPA_RUNTIME_OVERRIDES", env_values)
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_RUNTIME_OVERRIDES_FILE


def _read_runtime_overrides(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        data = json.loads(text)
    except OSError as exc:
        raise SettingsError(f"failed to read runtime overrides: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{path} must contain a JSON object") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must contain a JSON object")
    return data


def _coerce_field(name: str, value: Any) -> Any:
    """Coerce a runtime-override JSON value into the right type for the given field.

    Accepts type that the JSON serializer might use (bool/int/float/str/None) and
    raises SettingsError on bad shapes.
    """
    if name in {"interval_seconds", "quota_threshold", "expiry_threshold_days",
                "usage_timeout_seconds", "cpa_timeout_seconds", "max_retries",
                "worker_threads", "ui_port"}:
        if isinstance(value, bool):  # bool is subclass of int — reject explicitly
            raise SettingsError(f"{name} must be an integer")
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            if value.is_integer():
                return int(value)
            raise SettingsError(f"{name} must be an integer")
        if isinstance(value, str) and value.strip():
            try:
                return int(value)
            except ValueError as exc:
                raise SettingsError(f"{name} must be an integer") from exc
        raise SettingsError(f"{name} must be an integer")
    if name == "enable_refresh":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise SettingsError(f"{name} must be a boolean")
    if name in {"proxy", "ui_token"}:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        raise SettingsError(f"{name} must be a string or null")
    if name in {"cpa_endpoint", "cpa_token", "ui_host"}:
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise SettingsError(f"{name} must be a non-empty string")
    raise SettingsError(f"Unknown field: {name}")


def _validate_field(name: str, value: Any) -> Any:
    """Apply per-field constraint checks identical to load_settings."""
    if name == "interval_seconds" and value < 1:
        raise SettingsError("interval_seconds must be >= 1")
    if name == "quota_threshold" and not (0 <= value <= 100):
        raise SettingsError("quota_threshold must be in [0, 100]")
    if name == "expiry_threshold_days" and value < 0:
        raise SettingsError("expiry_threshold_days must be >= 0")
    if name == "usage_timeout_seconds" and value < 1:
        raise SettingsError("usage_timeout_seconds must be >= 1")
    if name == "cpa_timeout_seconds" and value < 1:
        raise SettingsError("cpa_timeout_seconds must be >= 1")
    if name == "max_retries" and not (0 <= value <= 5):
        raise SettingsError("max_retries must be in [0, 5]")
    if name == "worker_threads" and value < 1:
        raise SettingsError("worker_threads must be >= 1")
    if name == "ui_port" and not (1 <= value <= 65535):
        raise SettingsError("ui_port must be in [1, 65535]")
    if name == "cpa_endpoint" and not value.startswith(("http://", "https://")):
        raise SettingsError("cpa_endpoint must start with http:// or https://")
    return value


def load_settings(env_file: Path | None = None, runtime_overrides_file: Path | None = None) -> Settings:
    env_values = _read_project_env_file(env_file)
    endpoint = (_get_config_value("CPA_ENDPOINT", env_values) or "").strip().rstrip("/")
    token = (_get_config_value("CPA_TOKEN", env_values) or "").strip()
    proxy = (_get_config_value("CPA_PROXY", env_values) or "").strip() or None

    if not endpoint:
        raise SettingsError("CPA_ENDPOINT is required")
    if not token:
        raise SettingsError("CPA_TOKEN is required")
    if not endpoint.startswith(("http://", "https://")):
        raise SettingsError("CPA_ENDPOINT must start with http:// or https://")

    ui_token_raw = (_get_config_value("CPA_UI_TOKEN", env_values) or "").strip()

    settings = Settings(
        cpa_endpoint=endpoint,
        cpa_token=token,
        proxy=proxy,
        interval_seconds=_read_int("CPA_INTERVAL", DEFAULT_INTERVAL_SECONDS, env_values, minimum=1),
        quota_threshold=_read_int("CPA_QUOTA_THRESHOLD", DEFAULT_QUOTA_THRESHOLD, env_values, minimum=0, maximum=100),
        expiry_threshold_days=_read_int("CPA_EXPIRY_THRESHOLD_DAYS", DEFAULT_EXPIRY_THRESHOLD_DAYS, env_values, minimum=0),
        usage_timeout_seconds=_read_int("CPA_USAGE_TIMEOUT", DEFAULT_USAGE_TIMEOUT_SECONDS, env_values, minimum=1),
        cpa_timeout_seconds=_read_int("CPA_HTTP_TIMEOUT", DEFAULT_CPA_TIMEOUT_SECONDS, env_values, minimum=1),
        max_retries=_read_int("CPA_MAX_RETRIES", DEFAULT_MAX_RETRIES, env_values, minimum=0, maximum=5),
        worker_threads=_read_int("CPA_WORKER_THREADS", DEFAULT_WORKER_THREADS, env_values, minimum=1),
        enable_refresh=_read_bool("CPA_ENABLE_REFRESH", DEFAULT_ENABLE_REFRESH, env_values),
        ui_host=(_get_config_value("CPA_UI_HOST", env_values) or DEFAULT_UI_HOST).strip() or DEFAULT_UI_HOST,
        ui_port=_read_int("CPA_UI_PORT", DEFAULT_UI_PORT, env_values, minimum=1, maximum=65535),
        ui_token=ui_token_raw or None,
    )

    overrides_path = runtime_overrides_file or _resolve_runtime_overrides_path(env_values)
    overrides = _read_runtime_overrides(overrides_path)
    valid_field_names = {f.name for f in fields(Settings)}
    for key, raw_value in overrides.items():
        if key not in valid_field_names:
            continue
        coerced = _coerce_field(key, raw_value)
        _validate_field(key, coerced)
        setattr(settings, key, coerced)
    return settings


class RuntimeSettings:
    """Thread-safe wrapper around Settings that supports runtime updates persisted
    to a JSON overrides file. Reading attributes is read-through to the current state.
    """

    def __init__(self, base: Settings, overrides_path: Path, *, env_sources: dict[str, bool] | None = None):
        self._lock = threading.RLock()
        self._current = base
        self._overrides_path = overrides_path
        self._env_sources = dict(env_sources or {})
        self._listeners: list[Any] = []

    @property
    def overrides_path(self) -> Path:
        return self._overrides_path

    def add_listener(self, callback) -> None:
        with self._lock:
            self._listeners.append(callback)

    def snapshot(self) -> Settings:
        with self._lock:
            return replace(self._current)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        with self._lock:
            return getattr(self._current, name)

    def update(self, updates: dict[str, Any]) -> Settings:
        if not isinstance(updates, dict) or not updates:
            raise SettingsError("updates must be a non-empty mapping")
        valid_field_names = {f.name for f in fields(Settings)}
        coerced: dict[str, Any] = {}
        for key, raw in updates.items():
            if key not in valid_field_names:
                raise SettingsError(f"Unknown field: {key}")
            value = _coerce_field(key, raw)
            _validate_field(key, value)
            coerced[key] = value
        with self._lock:
            new_settings = replace(self._current, **coerced)
            self._persist_overrides_locked(coerced)
            self._current = new_settings
            listeners = list(self._listeners)
            snapshot = replace(new_settings)
        for cb in listeners:
            try:
                cb(snapshot, coerced)
            except Exception:
                pass
        return snapshot

    def _persist_overrides_locked(self, coerced: dict[str, Any]) -> None:
        existing = _read_runtime_overrides(self._overrides_path)
        existing.update(coerced)
        tmp_path: Path | None = None
        try:
            self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._overrides_path.parent,
                prefix=f".{self._overrides_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                if os.name == "posix":
                    tmp_path.chmod(0o600)
                tmp.write(json.dumps(existing, indent=2, sort_keys=True))
                tmp.flush()
                os.fsync(tmp.fileno())
            tmp_path.replace(self._overrides_path)
            if os.name == "posix":
                self._overrides_path.chmod(0o600)
        except OSError as exc:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise SettingsError(f"failed to persist overrides: {exc}") from exc

    def field_sources(self) -> dict[str, str]:
        """Return where each field currently comes from: 'override' / 'env' / 'default'."""
        overrides = _read_runtime_overrides(self._overrides_path)
        result: dict[str, str] = {}
        for f in fields(Settings):
            if f.name in overrides:
                result[f.name] = "override"
            elif self._env_sources.get(f.name):
                result[f.name] = "env"
            else:
                result[f.name] = "default"
        return result


def _detect_env_sources(env_file: Path | None) -> dict[str, bool]:
    env_values = _read_project_env_file(env_file)
    name_map = {
        "cpa_endpoint": "CPA_ENDPOINT",
        "cpa_token": "CPA_TOKEN",
        "proxy": "CPA_PROXY",
        "interval_seconds": "CPA_INTERVAL",
        "quota_threshold": "CPA_QUOTA_THRESHOLD",
        "expiry_threshold_days": "CPA_EXPIRY_THRESHOLD_DAYS",
        "usage_timeout_seconds": "CPA_USAGE_TIMEOUT",
        "cpa_timeout_seconds": "CPA_HTTP_TIMEOUT",
        "max_retries": "CPA_MAX_RETRIES",
        "worker_threads": "CPA_WORKER_THREADS",
        "enable_refresh": "CPA_ENABLE_REFRESH",
        "ui_host": "CPA_UI_HOST",
        "ui_port": "CPA_UI_PORT",
        "ui_token": "CPA_UI_TOKEN",
    }
    sources: dict[str, bool] = {}
    for field_name, env_name in name_map.items():
        value = _get_config_value(env_name, env_values)
        sources[field_name] = value not in (None, "")
    return sources


def load_runtime_settings(env_file: Path | None = None, runtime_overrides_file: Path | None = None) -> RuntimeSettings:
    settings = load_settings(env_file=env_file, runtime_overrides_file=runtime_overrides_file)
    env_values = _read_project_env_file(env_file)
    overrides_path = runtime_overrides_file or _resolve_runtime_overrides_path(env_values)
    env_sources = _detect_env_sources(env_file)
    return RuntimeSettings(settings, overrides_path, env_sources=env_sources)
