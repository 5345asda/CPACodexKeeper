"""Command entrypoint for configured fast scanning."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING

from cpa_keeper import __version__

if TYPE_CHECKING:
    from cpa_keeper.application.fast_scan import FastScanService
    from cpa_keeper.application.fast_scan_scheduler import FastScanScheduler
    from cpa_keeper.application.results import InspectionResult
    from cpa_keeper.config.loader import LoadedRuntimeConfig
    from cpa_keeper.domain.reports import ProviderRunReport


LOGGER = logging.getLogger(__name__)

# Runtime imports stay inside functions so `--help` and `--version` never load
# configuration, HTTP clients, or provider code.


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpa-keeper",
        description="Configured fast scanning for CPA management APIs.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    runtime_options = argparse.ArgumentParser(add_help=False)
    runtime_options.add_argument("--config", type=Path, help="TOML behavior file")
    runtime_options.add_argument("--env-file", type=Path, help="dotenv secrets file")

    config = subparsers.add_parser("config", help="validate configuration")
    config_subparsers = config.add_subparsers(dest="config_command")
    config_subparsers.add_parser("validate", parents=[runtime_options], help="validate TOML and secrets")

    subparsers.add_parser("doctor", parents=[runtime_options], help="validate local runtime settings")
    subparsers.add_parser("scan", parents=[runtime_options], help="run one global fast scan")
    subparsers.add_parser("run", parents=[runtime_options], help="run one fast scan and enabled inspections")
    subparsers.add_parser("daemon", parents=[runtime_options], help="run scheduled maintenance")
    return parser


def _load_config(args: argparse.Namespace) -> LoadedRuntimeConfig:
    from cpa_keeper.config.loader import load_runtime_config

    return load_runtime_config(config_path=args.config, env_file=args.env_file)


def _print_reports(reports: Sequence[ProviderRunReport]) -> None:
    for report in reports:
        print(
            f"provider={report.provider_id} scanned={report.scanned} "
            f"matched={report.matched} applied={report.applied} "
            f"skipped={report.skipped} failed={report.failed}"
        )


def _exit_code(status: object) -> int:
    from cpa_keeper.application.results import RunStatus

    if status in {RunStatus.SUCCESS, RunStatus.EMPTY}:
        return 0
    if status is RunStatus.PARTIAL_FAILURE:
        return 1
    if status is RunStatus.UPSTREAM_FAILURE:
        return 3
    return 4


def _build_inspection_callback(runtime: LoadedRuntimeConfig, cpa_api: object, mutation_coordinator: object):
    """Build Codex inspection directly from the active TOML runtime settings."""
    from cpa_keeper.application.inspection_service import build_codex_inspection_service

    codex = runtime.behavior.providers.get("codex")
    if codex is None or codex.inspection is None:
        return lambda _provider_id, _snapshot: None
    inspection_service = build_codex_inspection_service(
        cpa_api,
        codex.inspection,
        proxy=runtime.connection.proxy,
        max_retries=runtime.behavior.control_plane.max_retries,
        mutation_coordinator=mutation_coordinator,
    )

    def inspect(_provider_id: str, snapshot: object) -> InspectionResult:
        metadata = tuple(
            item
            for item in snapshot.metadata
            if item.provider_id == "codex" and item.name not in snapshot.handled_resource_names
        )
        started = time.monotonic()
        result = inspection_service.inspect(metadata, expected_epochs=snapshot.resource_epochs)
        duration_ms = round((time.monotonic() - started) * 1000)
        for report in result.reports:
            LOGGER.info(
                "event=inspection_summary provider=%s scanned=%s matched=%s applied=%s skipped=%s failed=%s duration_ms=%s",
                report.provider_id,
                report.scanned,
                report.matched,
                report.applied,
                report.skipped,
                report.failed,
                duration_ms,
            )
        return result

    return inspect


def _build_services(runtime: LoadedRuntimeConfig) -> tuple[FastScanService, FastScanScheduler]:
    from cpa_keeper.application.fast_scan import FastScanService
    from cpa_keeper.application.fast_scan_scheduler import FastScanScheduler
    from cpa_keeper.application.mutation_coordinator import AuthFileMutationCoordinator
    from cpa_keeper.infrastructure.cpa_api import CpaApi

    cpa_api = CpaApi(
        endpoint=runtime.connection.endpoint,
        token=runtime.connection.token,
        proxy=runtime.connection.proxy,
        timeout_seconds=runtime.behavior.control_plane.timeout_seconds,
        max_retries=runtime.behavior.control_plane.max_retries,
    )
    mutation_coordinator = AuthFileMutationCoordinator()
    fast_scan = FastScanService(
        cpa_api,
        runtime.behavior,
        mutation_coordinator=mutation_coordinator,
    )
    return fast_scan, FastScanScheduler(
        fast_scan,
        runtime.behavior,
        inspect=_build_inspection_callback(runtime, cpa_api, mutation_coordinator),
    )


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "config":
        if args.config_command != "validate":
            raise ValueError("config requires the validate subcommand")
        _load_config(args)
        print("config valid")
        return 0

    runtime = _load_config(args)
    if args.command == "doctor":
        providers = ",".join(runtime.behavior.providers)
        print(
            f"doctor config=valid fast_scan_interval_seconds={runtime.behavior.fast_scan.interval_seconds} "
            f"providers={providers}"
        )
        return 0

    fast_scan, scheduler = _build_services(runtime)
    if args.command == "scan":
        result = fast_scan.scan()
        _print_reports(result.reports)
        return _exit_code(result.status)
    if args.command == "run":
        result = scheduler.run_once()
        _print_reports(result.reports)
        return _exit_code(result.status)
    if args.command == "daemon":
        scheduler.run_forever()
        return 0
    raise ValueError("unsupported command")


def main(argv: Sequence[str] | None = None) -> int:
    """Run commands with concise errors that never include credential values."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args, parser)
    except (OSError, ValueError) as exc:
        print(f"configuration_error: {exc}", file=sys.stderr)
        return 2
    except Exception:
        LOGGER.error("event=internal_error outcome=failed")
        print("internal_error", file=sys.stderr)
        return 4
