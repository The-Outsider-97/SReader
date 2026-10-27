"""``python -m slai_reader [FILE ...]`` - start SLAI Reader."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

from . import APP_NAME, __version__
from .config import Settings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m slai_reader", description="SLAI Reader - read and annotate documents on your own computer")
    p.add_argument("files", nargs="*", help="documents to open")
    p.add_argument("--handoff", action="append", default=[], metavar="FILE", help="open the document named by a slai-handoff/1 file (from Documaster or Writer)")
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (loopback only)")
    p.add_argument("--port", type=int, default=8767)
    p.add_argument("--slai-root", help="path to the SLAI checkout (default: found automatically)")
    p.add_argument("--data-dir", help="where Reader keeps its library, annotations and backups")
    p.add_argument("--output-dir", help="where exports are written")
    p.add_argument("--agents", choices=("auto", "none", "strict"), default=None, help="optional assistant: auto (default), none (never use agents), strict")
    p.add_argument("--allow-network-paths", action="store_true", help="allow opening files from network shares (UNC paths)")
    p.add_argument("--writer-command", help="command that starts SLAI Writer; {handoff} is replaced by the handoff file")
    p.add_argument("--diagnose-storage", action="store_true", help="check, step by step, that settings can be saved in the data folder, then exit (add --json for machine-readable output)")
    p.add_argument("--json", action="store_true", help="with --diagnose-storage: print the report as JSON")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    p.add_argument("--token", help="fixed access token (for tests; random by default)")
    p.add_argument("--client-dir", help="folder with the built web client")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = p.parse_args(argv)
    logging.getLogger("pypdf").setLevel(logging.ERROR)

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("Refusing to listen on a non-loopback address: Reader handles private documents and is a local app.", file=sys.stderr)
        return 2
    s = Settings(host=args.host, port=args.port)
    if args.data_dir:
        s.data_dir = Path(args.data_dir).expanduser().resolve()
    if args.output_dir:
        s.output_dir = Path(args.output_dir).expanduser().resolve()
    if args.token:
        s.token = args.token
    if args.agents:
        s.agents = args.agents
    if args.slai_root or os.environ.get("SLAI_ROOT"):
        s.slai_root = Path(args.slai_root or os.environ["SLAI_ROOT"]).expanduser().resolve()
    if args.writer_command:
        s.writer_command = args.writer_command
    s.allow_network_paths = args.allow_network_paths
    s.open_browser = not args.no_browser

    if args.diagnose_storage:
        import json as _json
        from .diagnose import diagnose, format_report
        report = diagnose(s.data_dir)
        print(_json.dumps(report, indent=2, default=str) if args.json else format_report(report))
        return 0 if report["ok"] else 1

    from .errors import ReaderError
    from .app import ReaderApp
    from .server import ReaderHTTPServer
    try:
        app = ReaderApp(s)
    except ReaderError as exc:
        print(f"Reader needs the SLAI checkout: {exc.message}", file=sys.stderr)
        return 2
    app.pending = [("path", str(Path(f).expanduser())) for f in args.files] + [("handoff", str(Path(f).expanduser())) for f in args.handoff]
    client = Path(args.client_dir).expanduser().resolve() if args.client_dir else Path(__file__).resolve().parent.parent / "dist"
    if not (client / "index.html").is_file():
        print(f"The web client was not found in {client}. Run 'npm install && npm run build' in the Reader folder.", file=sys.stderr)
        app.close()
        return 2
    try:
        server = ReaderHTTPServer(app, client)
    except OSError as exc:
        print(f"Cannot listen on {s.host}:{s.port}: {exc}. Try --port.", file=sys.stderr)
        app.close()
        return 2
    url = f"{server.base_url}/?token={s.token}"
    print(f"{APP_NAME} {__version__}\n  SLAI root : {s.slai_root}\n  Data      : {s.data_dir}\n  Exports   : {s.output_dir}\n  Open      : {url}", flush=True)
    for note in app.startup_notes:
        print(f"  notice    : {note}", flush=True)
    if s.open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    import signal

    def stop(*_: object) -> None:                       # a polite "please stop" (Ctrl+Break, taskkill without /f, a service manager) ends cleanly
        threading.Thread(target=server.shutdown, daemon=True).start()

    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), stop)
            except (ValueError, OSError):
                pass
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
