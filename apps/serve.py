"""Start the demo applications, each in its own process.

    python -m apps.serve                 # all three, foreground, Ctrl-C to stop
    python -m apps.serve --only helpdesk  # one of them
    python -m apps.serve --print-tokens   # show the generated bearer tokens

Every service binds to loopback only.  They are ordinary independent processes:
stopping one leaves the others running, which is what makes MORPH's degraded-mode
and partial-failure behaviour demonstrable.
"""
from __future__ import annotations

import argparse
import importlib
import os
import signal
import subprocess
import sys
import time
from typing import Iterable

SERVICES = ("storefront", "helpdesk", "warehouse")


def spec_of(name: str):
    return importlib.import_module(f"apps.{name}").SPEC


def _child(name: str, host: str, *, detached: bool = False) -> subprocess.Popen:
    """Start one service.

    ``detached`` is for a caller that returns while the service keeps running. It
    closes the child's stdio and puts it in its own session, because a child that
    inherits the terminal holds the pipe open after its parent exits — so
    ``... | tee log`` hangs forever on a command that already finished.
    """
    spec = spec_of(name)
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    extra: dict[str, object] = {}
    if detached:
        extra = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                 "stderr": subprocess.DEVNULL, "start_new_session": True}
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", f"apps.{name}:app",
         "--host", host, "--port", str(spec.port), "--log-level", "warning"],
        env=env, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        **extra,  # type: ignore[arg-type]
    )


def wait_ready(names: Iterable[str], host: str = "127.0.0.1", timeout: float = 25.0) -> bool:
    import urllib.error
    import urllib.request
    deadline = time.time() + timeout
    pending = list(names)
    while pending and time.time() < deadline:
        for name in list(pending):
            url = f"http://{host}:{spec_of(name).port}/health"
            try:
                with urllib.request.urlopen(url, timeout=1.5) as resp:  # noqa: S310 - loopback only
                    if resp.status == 200:
                        pending.remove(name)
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(0.25)
    return not pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the MORPH demo applications.")
    parser.add_argument("--only", action="append", choices=SERVICES, help="run just this service")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (loopback by default)")
    parser.add_argument("--print-tokens", action="store_true", help="print tokens and exit")
    args = parser.parse_args(argv)
    names = args.only or list(SERVICES)

    if args.print_tokens:
        from apps._runtime import token_for
        for name in names:
            spec = spec_of(name)
            print(f"{spec.token_env}={token_for(spec)}")
        return 0

    children = [(name, _child(name, args.host)) for name in names]
    for name in names:
        spec = spec_of(name)
        print(f"  {spec.label:<12} http://{args.host}:{spec.port}", flush=True)
    if not wait_ready(names, args.host):
        print("one or more services did not become ready", file=sys.stderr)

    def shutdown(*_: object) -> None:
        for _name, proc in children:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGINT, lambda *_: (shutdown(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (shutdown(), sys.exit(0)))
    try:
        while True:
            time.sleep(1)
            for name, proc in children:
                if proc.poll() is not None:
                    print(f"{name} exited with {proc.returncode}", file=sys.stderr)
                    shutdown()
                    return proc.returncode or 1
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
