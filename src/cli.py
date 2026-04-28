from .maintainer import CPACodexKeeper
from .settings import SettingsError, load_runtime_settings, load_settings


def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(description="CPACodexKeeper")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，不实际修改 / Dry run")
    parser.add_argument("--daemon", action="store_true", default=True, help="守护模式，默认开启 / Run forever")
    parser.add_argument("--once", dest="daemon", action="store_false", help="仅执行一轮后退出 / Run once")
    parser.add_argument("--no-web", action="store_true", help="不启动 Web 管理面板 / Disable Keeper UI server")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        if args.daemon and not args.no_web:
            settings = load_runtime_settings()
        else:
            settings = load_settings()
    except SettingsError as exc:
        parser.exit(status=2, message=f"Configuration error: {exc}\n")

    maintainer = CPACodexKeeper(settings=settings, dry_run=args.dry_run)
    if args.daemon:
        if not args.no_web:
            from .web import serve_app

            snap = settings.snapshot()
            print(f"[*] Keeper UI listening on http://{snap.ui_host}:{snap.ui_port}/")
            serve_app(maintainer, settings)
        maintainer.run_forever()
        return 0
    maintainer.run()
    return 0
