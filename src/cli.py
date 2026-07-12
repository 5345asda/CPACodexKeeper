from .maintainer import CPACodexKeeper
from .settings import SettingsError, load_settings


def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(description="CPACodexKeeper", allow_abbrev=False)
    parser.add_argument("--dry-run", action="store_true", help="演练模式，不实际修改 / Dry run")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--daemon", dest="daemon", action="store_true", help="守护模式，默认开启 / Run forever")
    mode_group.add_argument("--once", dest="daemon", action="store_false", help="仅执行一轮后退出 / Run once")
    mode_group.add_argument(
        "--xai-error-sweep-once",
        action="store_true",
        help=(
            "仅扫尾删除 xAI chat endpoint 权限拒绝项后退出；需 "
            "CPA_XAI_PERMISSION_DENIED_DELETE_ENABLED=true（默认 false） / "
            "Sweep xAI errors once; requires "
            "CPA_XAI_PERMISSION_DENIED_DELETE_ENABLED=true (default false)"
        ),
    )
    parser.set_defaults(daemon=True, xai_error_sweep_once=False)
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        settings = load_settings()
    except SettingsError as exc:
        parser.exit(status=2, message=f"Configuration error: {exc}\n")

    if args.xai_error_sweep_once and not settings.xai_permission_denied_delete_enabled:
        parser.error(
            "--xai-error-sweep-once requires "
            "CPA_XAI_PERMISSION_DENIED_DELETE_ENABLED=true"
        )

    maintainer = CPACodexKeeper(settings=settings, dry_run=args.dry_run)
    if args.xai_error_sweep_once:
        result = maintainer.sweep_error_status_once(allowed_types={"xai"})
        return int(result.get("failed", 0) > 0)
    if args.daemon:
        maintainer.run_forever(interval_seconds=settings.interval_seconds)
        return 0
    maintainer.run()
    return 0
