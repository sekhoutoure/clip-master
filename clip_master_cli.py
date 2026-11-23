"""`clip-master` command line: serve, doctor, install, version."""
from __future__ import annotations

import argparse
import sys
import webbrowser

__version__ = "0.1.0"


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    import config

    host, port = args.host or config.HOST, args.port or config.PORT
    if args.open:
        import threading

        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    uvicorn.run("server:app", host=host, port=port, reload=False)
    return 0


def _doctor(args: argparse.Namespace) -> int:
    import bootstrap

    return bootstrap.main(["--check"] + (["--json"] if args.json else []) + (["--with-reference"] if args.with_reference else []))


def _install(args: argparse.Namespace) -> int:
    import bootstrap

    return bootstrap.main(["--with-reference"] if args.with_reference else [])


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

    i = sub.add_parser("install", help="finish the setup: .env, Remotion, model weights, third-party tools")
    i.add_argument("--with-reference", action="store_true")
    i.set_defaults(fn=_install)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
