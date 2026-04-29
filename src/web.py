from contextlib import contextmanager
import threading
import time
from pathlib import Path
from secrets import compare_digest
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .maintainer import CPACodexKeeper
from .reports import TokenReport
from .settings import POLICY_FIELDS, TRANSPORT_FIELDS, RuntimeSettings, SettingsError
from .utils import format_seconds, get_expired_remaining_with_status

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
INDEX_FILE = STATIC_DIR / "index.html"

# Fields that we explicitly do NOT echo back over the wire.
SECRET_FIELDS = {"cpa_token", "ui_token"}


def _redact_settings(values: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(values)
    for field in SECRET_FIELDS:
        if redacted.get(field):
            redacted[field] = "***"
    return redacted


def _report_to_dict(report: TokenReport) -> dict[str, Any]:
    payload = report.as_dict()
    if report.expiry_remaining_seconds is not None:
        payload["expiry_remaining_human"] = format_seconds(report.expiry_remaining_seconds)
    else:
        payload["expiry_remaining_human"] = None
    return payload


def create_app(keeper: CPACodexKeeper, settings: RuntimeSettings) -> FastAPI:
    app = FastAPI(title="CPACodexKeeper", version="0.1.0", docs_url=None, redoc_url=None)
    scan_lock = threading.Lock()

    def scan_in_progress() -> bool:
        return scan_lock.locked() or keeper.is_running()

    @contextmanager
    def manual_operation_guard():
        if not scan_lock.acquire(blocking=False):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="scan already in progress")
        if not keeper.try_acquire_operation_lock():
            scan_lock.release()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="scan already in progress")
        try:
            yield
        finally:
            keeper.release_operation_lock()
            scan_lock.release()

    def require_bearer(request: Request, token: str) -> None:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
        supplied = auth.split(" ", 1)[1].strip()
        if not compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")

    def authorize_read(request: Request) -> None:
        token = settings.snapshot().ui_token
        if not token:
            return
        require_bearer(request, token)

    def authorize_write(request: Request) -> None:
        token = settings.snapshot().ui_token
        if not token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CPA_UI_TOKEN is required for write operations",
            )
        require_bearer(request, token)

    @app.get("/", include_in_schema=False)
    def root():
        if INDEX_FILE.exists():
            return FileResponse(INDEX_FILE)
        return JSONResponse({"error": "static UI not built"}, status_code=500)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/state")
    def get_state(_: None = Depends(authorize_read)):
        snap = settings.snapshot()
        snap_dict = _redact_settings(snap.as_dict())
        return {
            "stats": keeper._stats_snapshot(),
            "reports": [_report_to_dict(r) for r in keeper.reports.all()],
            "settings": snap_dict,
            "field_sources": settings.field_sources(),
            "policy_fields": list(POLICY_FIELDS),
            "transport_fields": list(TRANSPORT_FIELDS),
            "secret_fields": sorted(SECRET_FIELDS),
            "last_run_started_at": keeper.last_run_started_at,
            "last_run_finished_at": keeper.last_run_finished_at,
            "now": time.time(),
            "scan_in_progress": scan_in_progress(),
            "dry_run": keeper.dry_run,
            "overrides_path": str(settings.overrides_path),
            "write_auth_required": True,
            "write_auth_configured": bool(snap.ui_token),
        }

    @app.post("/api/scan")
    def trigger_scan(_: None = Depends(authorize_write)):
        if not scan_lock.acquire(blocking=False):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="scan already in progress")
        if keeper.is_running():
            scan_lock.release()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="scan already in progress")

        def _runner():
            try:
                keeper.run()
            finally:
                scan_lock.release()

        threading.Thread(target=_runner, name="keeper-manual-scan", daemon=True).start()
        return {"started": True, "started_at": time.time()}

    @app.post("/api/scan/{name}")
    def trigger_scan_one(name: str, _: None = Depends(authorize_write)):
        with manual_operation_guard():
            try:
                report = keeper.process_one(name)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        return _report_to_dict(report)

    @app.patch("/api/tokens/{name}")
    def patch_token(name: str, payload: dict[str, Any], _: None = Depends(authorize_write)):
        with manual_operation_guard():
            if "disabled" not in payload:
                raise HTTPException(status_code=400, detail="disabled is required")
            if not isinstance(payload["disabled"], bool):
                raise HTTPException(status_code=400, detail="disabled must be a boolean")
            disabled = payload["disabled"]
            ok = keeper.set_disabled_status(name, disabled=disabled)
            if not ok:
                raise HTTPException(status_code=502, detail="cpa rejected status update")
            report = keeper.reports.get(name)
            if report is not None:
                if keeper.dry_run:
                    report.last_actions.append(f"DRY: 将{'禁用' if disabled else '启用'}")
                    report.last_outcome = "skipped"
                else:
                    report.disabled = disabled
                    report.last_actions.append(f"MANUAL: {'禁用' if disabled else '启用'}")
                    report.last_outcome = "disabled" if disabled else "enabled"
                report.checked_at = time.time()
                keeper.reports.upsert(report)
        return {"ok": True, "disabled": disabled, "dry_run": keeper.dry_run}

    @app.delete("/api/tokens/{name}")
    def delete_token(name: str, _: None = Depends(authorize_write)):
        with manual_operation_guard():
            ok = keeper.delete_token(name)
            if not ok:
                raise HTTPException(status_code=502, detail="cpa rejected delete")
            if keeper.dry_run:
                report = keeper.reports.get(name)
                if report is not None:
                    report.last_actions.append("DRY: 将删除")
                    report.last_outcome = "skipped"
                    report.checked_at = time.time()
                    keeper.reports.upsert(report)
            else:
                keeper.reports.remove(name)
        return {"ok": True, "dry_run": keeper.dry_run}

    @app.post("/api/tokens/{name}/refresh")
    def refresh_token(name: str, _: None = Depends(authorize_write)):
        with manual_operation_guard():
            token_data = keeper.cpa_client.get_auth_file(name)
            if not token_data:
                raise HTTPException(status_code=404, detail="token not found")
            if keeper.dry_run:
                report = keeper.reports.get(name)
                if report is not None:
                    report.last_actions.append("DRY: 将刷新并上传更新")
                    report.last_outcome = "skipped"
                    report.checked_at = time.time()
                    keeper.reports.upsert(report)
                return {"ok": True, "message": "dry-run: refresh skipped", "dry_run": True}
            success, new_data, msg = keeper.try_refresh(token_data)
            if not success:
                raise HTTPException(status_code=502, detail=msg)
            if not keeper.upload_updated_token(name, new_data):
                raise HTTPException(status_code=502, detail="upload failed after refresh")
            report = keeper.reports.get(name)
            if report is not None:
                _, remaining, known = get_expired_remaining_with_status(new_data)
                report.expiry = new_data.get("expired") or report.expiry
                report.expiry_remaining_seconds = int(remaining) if known else None
                report.last_actions.append(f"MANUAL REFRESH: {msg}")
                report.last_outcome = "refreshed"
                report.checked_at = time.time()
                keeper.reports.upsert(report)
        return {"ok": True, "message": msg, "dry_run": False}

    @app.get("/api/config")
    def get_config(_: None = Depends(authorize_read)):
        snap = settings.snapshot()
        return {
            "values": _redact_settings(snap.as_dict()),
            "sources": settings.field_sources(),
            "policy_fields": list(POLICY_FIELDS),
            "transport_fields": list(TRANSPORT_FIELDS),
            "secret_fields": sorted(SECRET_FIELDS),
            "overrides_path": str(settings.overrides_path),
        }

    @app.put("/api/config")
    def put_config(payload: dict[str, Any], _: None = Depends(authorize_write)):
        if not isinstance(payload, dict) or not payload:
            raise HTTPException(status_code=400, detail="empty payload")
        try:
            new_settings = settings.update(payload)
        except SettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        restart_required = sorted(set(payload.keys()) & set(TRANSPORT_FIELDS))
        return {
            "values": _redact_settings(new_settings.as_dict()),
            "sources": settings.field_sources(),
            "restart_required_fields": restart_required,
        }

    return app


def serve_app(keeper: CPACodexKeeper, settings: RuntimeSettings) -> threading.Thread:
    """Launch a uvicorn server in a daemon thread. Returns the thread."""
    import uvicorn

    snap = settings.snapshot()
    app = create_app(keeper, settings)
    config = uvicorn.Config(app, host=snap.ui_host, port=snap.ui_port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    def _run():
        try:
            server.run()
        except Exception as exc:
            print(f"[ERROR] Keeper UI server crashed: {exc}")

    thread = threading.Thread(target=_run, name="keeper-ui", daemon=True)
    thread.start()
    return thread
