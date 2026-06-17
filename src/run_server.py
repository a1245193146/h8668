"""run_server.py — Serve the backup dashboard on localhost:8080.

Usage:
  python src/run_server.py                      # serve only (dashboard-only mode)
  python src/run_server.py --dashboard-only     # same
  python src/run_server.py --port 9090          # custom port
"""
from __future__ import annotations

import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))      # .../src
_ROOT = _os.path.dirname(_HERE)                            # project root
for _p in (_HERE, _os.path.join(_ROOT, "vendor")):
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.insert(0, _p)

import argparse
import http.server
import logging
import os
import sys
from pathlib import Path


logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run_server",
                                description="Serve the backup dashboard")
    p.add_argument("--dashboard-only", action="store_true",
                   help="Only start the HTTP server (default behaviour)")
    p.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    p.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    return p


def serve(host: str = "127.0.0.1", port: int = 8080, dashboard_dir: str | None = None) -> None:
    """Start a simple HTTP server serving the dashboard directory."""
    if dashboard_dir is None:
        # Locate dashboard/ relative to project root (one level above src/)
        src_dir = Path(__file__).parent
        project_root = src_dir.parent
        dashboard_dir = str(project_root / "dashboard")

    if not Path(dashboard_dir).is_dir():
        logger.error("Dashboard directory not found: %s", dashboard_dir)
        sys.exit(1)

    os.chdir(dashboard_dir)
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass

    handler = QuietHandler

    with http.server.HTTPServer((host, port), handler) as httpd:
        logger.info("Dashboard → http://%s:%s/", host, port)
        logger.info("Serving from: %s", dashboard_dir)
        logger.info("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Stopped.")


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    serve(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
