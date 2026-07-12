from .maintainer import CPACodexKeeper
from .settings import SettingsError, load_settings


def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(description="CPACodexKeeper")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，不实际修改 / Dry run")
    parser.add_argument("--daemon", action="store_true", default=True, help="守护模式，默认开启 / Run forever")
    parser.add_argument("--once", dest="daemon", action="store_false", help="仅执行一轮后退出 / Run once")
    parser.add_argument(
        "--xai-error-sweep-once",
        action="store_true",
        help="仅扫尾删除 xAI chat endpoint 权限拒绝项后退出 / Sweep xAI errors once",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        settings = load_settings()
    except SettingsError as exc:
        parser.exit(status=2, message=f"Configuration error: {exc}\n")

    maintainer = CPACodexKeeper(settings=settings, dry_run=args.dry_run)
    if args.xai_error_sweep_once:
        maintainer.sweep_error_status_once(allowed_types={"xai"})
        return 0
    if args.daemon:
        maintainer.run_forever(interval_seconds=settings.interval_seconds)
        return 0
    maintainer.run()
    return 0
