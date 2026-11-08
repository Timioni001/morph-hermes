"""Serve the browser-test harness: MORPH's real dashboard bundle and real API.

    python -m tests.e2e.harness.serve --port 8820

Routes:
  /                      the harness page (the Hermes dashboard contract, nothing else)
  /static/*              dashboard/dist and the vendored React UMD builds
  /api/plugins/morph/*   MORPH's actual FastAPI router, mounted exactly as Hermes mounts it
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HARNESS = ROOT / "tests" / "e2e" / "harness"


def _plugin_router():
    spec = importlib.util.spec_from_file_location(
        "morph_dashboard_api", ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.router


def build() -> FastAPI:
    app = FastAPI(title="MORPH dashboard harness", docs_url=None, redoc_url=None)
    app.include_router(_plugin_router(), prefix="/api/plugins/morph")

    static = HARNESS / "static"
    static.mkdir(exist_ok=True)
    for name, source in (("index.js", ROOT / "dashboard" / "dist" / "index.js"),
                         ("style.css", ROOT / "dashboard" / "dist" / "style.css")):
        target = static / name
        if source.is_file():
            target.write_bytes(source.read_bytes())
    for name in ("react.production.min.js", "react-dom.production.min.js"):
        source = HARNESS / "vendor" / name
        if source.is_file():
            (static / name).write_bytes(source.read_bytes())

    app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(str(HARNESS / "host.html"))

    return app


app = build()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8820)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(build(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
