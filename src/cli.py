import threading

from .maintainer import CPACodexKeeper, PriorityCoordinator
from .settings import SettingsError, load_settings


def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(description="CPACodexKeeper")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，不实际修改 / Dry run")
    parser.add_argument("--daemon", action="store_true", default=True, help="守护模式，默认开启 / Run forever")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", dest="daemon", action="store_false", help="仅执行一轮后退出 / Run once")
    mode_group.add_argument("-monitor", dest="monitor", action="store_true", help="仅启动日志巡检与定时复查 / Monitor only")
    parser.set_defaults(monitor=False)
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        settings = load_settings()
    except SettingsError as exc:
        parser.exit(status=2, message=f"Configuration error: {exc}\n")

    coordinator = PriorityCoordinator()
    logger = None
    maintainer = CPACodexKeeper(settings=settings, dry_run=args.dry_run, coordinator=coordinator, logger=logger)
    if args.monitor:
        maintainer._start_tracked_rechecks()
        maintainer.run_fill_forever(interval_seconds=settings.fill_interval_seconds)
        return 0
    if args.daemon:
        maintainer._start_tracked_rechecks()
        if settings.usage_query_interval_seconds > 0:
            fill_maintainer = CPACodexKeeper(settings=settings, dry_run=args.dry_run, coordinator=coordinator, logger=maintainer.logger)
            fill_thread = threading.Thread(
                target=fill_maintainer.run_fill_forever,
                kwargs={"interval_seconds": settings.fill_interval_seconds},
                daemon=True,
            )
            fill_thread.start()
        maintainer.run_forever(interval_seconds=settings.interval_seconds)
        return 0
    maintainer.run()
    return 0
