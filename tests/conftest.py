"""Shared fixtures.

Two kinds of test environment:

* **isolated** — MORPH's own storage in a temp directory and an in-process
  connector. Fast, deterministic, no sockets. Most unit tests use this.
* **live_apps** — the three real demo applications in real subprocesses on their
  own ports with their own databases, which is the only way to test what MORPH
  actually claims: schema introspection over HTTP, cross-application joins,
  optimistic concurrency against a separate process, and read-back verification.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _reset_morph() -> None:
    from morph import config, engine, registry, store
    config.settings.cache_clear()
    registry.reset()
    engine.invalidate()
    store.reset_cache()
    from morph import actions
    actions.reset_rate_limits()


@pytest.fixture
def morph_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private MORPH data directory; nothing leaks between tests."""
    home = tmp_path / "morph-data"
    home.mkdir()
    monkeypatch.setenv("MORPH_DATA_DIR", str(home))
    monkeypatch.delenv("MORPH_CONFIG", raising=False)
    monkeypatch.delenv("MORPH_CONFIG_FILE", raising=False)
    _reset_morph()
    yield home
    _reset_morph()


FIXTURE_SCHEMA: dict[str, Any] = {
    "label": "Fixture CRM",
    "description": "A tiny in-process source used by the unit tests.",
    "entities": [
        {
            "name": "deals", "label": "Deals", "primary_key": "id", "title_field": "name",
            "fields": [
                {"name": "id", "type": "id", "role": "key"},
                {"name": "name", "type": "string", "role": "title"},
                {"name": "stage", "type": "enum", "role": "status",
                 "values": ["new", "qualified", "won", "lost"]},
                {"name": "amount", "type": "money", "role": "amount", "unit": "USD"},
                {"name": "owner", "type": "string"},
                {"name": "flagged", "type": "bool", "role": "flag", "writable": True},
                {"name": "note", "type": "text", "writable": True},
            ],
            "operations": [
                {"id": "flag_deal", "label": "Flag deal", "description": "Flag it.",
                 "risk": "low", "effect": {"flagged": True, "note": "{reason}"},
                 "params": [{"name": "reason", "type": "text", "required": True,
                             "max_length": 100}],
                 "precondition": [{"field": "flagged", "op": "eq", "value": False,
                                   "message": "already flagged"}]},
                {"id": "close_deal", "label": "Close deal", "description": "Mark it won.",
                 "risk": "high", "reversible": False, "effect": {"stage": "won"}},
            ],
            "relationships": [{"name": "owner_person", "target_entity": "people",
                               "local_field": "owner", "target_field": "id",
                               "cardinality": "one"}],
            "capabilities": {"filter_ops": ["eq", "neq", "in", "gt", "gte", "lt", "lte",
                                            "contains", "exists", "nin"],
                             "sort": True, "paginate": True, "max_page_size": 100},
        },
        {
            "name": "people", "label": "People", "primary_key": "id", "title_field": "name",
            "fields": [
                {"name": "id", "type": "id", "role": "key"},
                {"name": "name", "type": "string", "role": "title"},
                {"name": "team", "type": "enum", "values": ["east", "west"], "role": "status"},
            ],
            "capabilities": {"filter_ops": ["eq", "in"], "sort": True, "paginate": True},
        },
    ],
}

FIXTURE_RECORDS: dict[str, list[dict[str, Any]]] = {
    "deals": [
        {"id": "D-1", "name": "Acme renewal", "stage": "qualified", "amount": 1200.0,
         "owner": "P-1", "flagged": False, "note": None},
        {"id": "D-2", "name": "Globex expansion", "stage": "new", "amount": 8400.0,
         "owner": "P-2", "flagged": False, "note": None},
        {"id": "D-3", "name": "Initech pilot", "stage": "won", "amount": 300.0,
         "owner": "P-1", "flagged": True, "note": "already looked at"},
    ],
    "people": [
        {"id": "P-1", "name": "Dana", "team": "east"},
        {"id": "P-2", "name": "Rafa", "team": "west"},
    ],
}


@pytest.fixture
def memory_config(morph_home: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Configure MORPH with a single in-process connector."""
    config = {
        "allow_loopback": False,
        "connectors": [{"id": "crm", "kind": "memory", "label": "Fixture CRM",
                        "options": {"schema": FIXTURE_SCHEMA, "records": FIXTURE_RECORDS}}],
        "links": [],
        "default_role": "operator",
        "roles": {"operator": {"allow": ["*"], "deny": [], "max_risk": "medium",
                               "can_approve": True, "label": "Operations lead"}},
    }
    monkeypatch.setenv("MORPH_CONFIG", json.dumps(config))
    _reset_morph()
    return config


@pytest.fixture
def graph(memory_config: dict[str, Any]):
    from morph import engine
    return engine.graph(refresh=True)


class LiveApps:
    """The three demo applications, running for real."""

    def __init__(self, ports: dict[str, int], tokens: dict[str, str], data_dir: Path) -> None:
        self.ports = ports
        self.tokens = tokens
        self.data_dir = data_dir

    def url(self, app: str) -> str:
        return f"http://127.0.0.1:{self.ports[app]}"

    def config(self, *, only: tuple[str, ...] = ("storefront", "helpdesk", "warehouse")
               ) -> dict[str, Any]:
        labels = {"storefront": "Storefront", "helpdesk": "Helpdesk", "warehouse": "Warehouse"}
        return {
            "allow_loopback": True,
            "allowed_hosts": ["127.0.0.1"],
            "connectors": [
                {"id": app, "kind": "http-rest", "label": labels[app],
                 "base_url": self.url(app), "token_env": f"MORPH_{app.upper()}_TOKEN",
                 "timeout": 10.0}
                for app in only],
            "links": [
                {"name": "support_cases", "source": "helpdesk.cases.order_ref",
                 "target": "storefront.orders.id", "cardinality": "many"},
                {"name": "shipments", "source": "warehouse.shipments.order_ref",
                 "target": "storefront.orders.id", "cardinality": "many"},
            ],
            "default_role": "operator",
            "roles": {"operator": {"allow": ["*"], "deny": ["storefront.orders.issue_refund"],
                                   "max_risk": "medium", "can_approve": True,
                                   "label": "Operations lead"}},
        }


@pytest.fixture(scope="session")
def live_apps(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveApps]:
    data_dir = tmp_path_factory.mktemp("demo-apps")
    ports = {name: _free_port() for name in ("storefront", "helpdesk", "warehouse")}
    env = dict(os.environ)
    env["MORPH_DEMO_DATA_DIR"] = str(data_dir)
    for name in ports:
        env.pop(f"MORPH_{name.upper()}_TOKEN", None)
    processes = []
    for name, port in ports.items():
        processes.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", f"apps.{name}:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    try:
        _wait_for(ports)
        tokens = {name: (data_dir / f"{name}.token").read_text(encoding="utf-8").strip()
                  for name in ports}
        yield LiveApps(ports, tokens, data_dir)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def _wait_for(ports: dict[str, int], timeout: float = 40.0) -> None:
    import urllib.error
    import urllib.request
    deadline = time.time() + timeout
    pending = dict(ports)
    while pending and time.time() < deadline:
        for name, port in list(pending.items()):
            try:
                with urllib.request.urlopen(  # noqa: S310 - loopback only
                        f"http://127.0.0.1:{port}/health", timeout=1.5) as response:
                    if response.status == 200:
                        pending.pop(name)
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(0.2)
    if pending:
        raise RuntimeError(f"demo applications did not start: {', '.join(pending)}")


@pytest.fixture
def live_config(live_apps: LiveApps, morph_home: Path,
                monkeypatch: pytest.MonkeyPatch) -> LiveApps:
    """Point MORPH at the live demo applications."""
    for name, token in live_apps.tokens.items():
        monkeypatch.setenv(f"MORPH_{name.upper()}_TOKEN", token)
    monkeypatch.setenv("MORPH_CONFIG", json.dumps(live_apps.config()))
    _reset_morph()
    return live_apps
