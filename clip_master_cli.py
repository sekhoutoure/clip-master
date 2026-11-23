"""`clip-master` command line: serve, doctor, install, clean, version."""
from __future__ import annotations

import argparse
import sys
import webbrowser

__version__ = "0.1.1"


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    import config

    host, port = args.host or config.HOST, args.port or config.PORT
    import security

    try:
        security.check_startup(host, config.TOKEN)
    except RuntimeError as exc:
        print(f"clip-master: {exc}", file=sys.stderr)
        return 2
    # server.py reads HOST/PORT from config when it is imported: keep it in line with --host / --port
    config.HOST, config.PORT = host, port
    if args.open:
        import threading

        shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{shown}:{port}/")).start()
    uvicorn.run("server:app", host=host, port=port, reload=False)
    return 0


def _doctor(args: argparse.Namespace) -> int:
    import bootstrap

    return bootstrap.main(["--check"] + (["--json"] if args.json else []) + (["--with-reference"] if args.with_reference else []))


def _install(args: argparse.Namespace) -> int:
    import bootstrap

    return bootstrap.main(["--with-reference"] if args.with_reference else [])


def _clean(args: argparse.Namespace) -> int:
    import json

    import cache_gc

    report = cache_gc.purge_now(all_unused=args.all, ttl_hours=args.ttl, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"{prefix}Freed {report['freed_human']} ({len(report['deleted'])} item(s)).")
        for item in report["deleted"]:
            print(f"  - {item['reason']}: {item['path']} ({cache_gc.human_bytes(item['bytes'])})")
        if not report["deleted"]:
            print("  Nothing to delete (running jobs, the queue file and fresh work are kept).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clip-master", description="CLIP MASTER - local 9:16 Shorts studio")
    ap.add_argument("--version", action="version", version=f"clip-master {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the web app")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--open", action="store_true", help="open the browser")
    s.set_defaults(fn=_serve)

    d = sub.add_parser("doctor", help="check the installation (changes nothing)")
    d.add_argument("--json", action="store_true")
    d.add_argument("--with-reference", action="store_true")
    d.set_defaults(fn=_doctor)

    i = sub.add_parser("install", help="finish the setup: .env, animated captions, model weights, third-party tools")
    i.add_argument("--with-reference", action="store_true")
    i.set_defaults(fn=_install)

    c = sub.add_parser("clean", help="purge old project workdirs, intermediates and exports")
    c.add_argument("--all", action="store_true", help="also delete unused work that is still within the TTL")
    c.add_argument("--ttl", type=int, default=None, help="override CLIP_MASTER_CACHE_TTL_HOURS")
    c.add_argument("--dry-run", action="store_true", help="list what would be deleted, change nothing")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=_clean)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
