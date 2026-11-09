"""``hermes morph up`` — bring the configured applications up.

MORPH connects to applications it does not own, so most of what it talks to is not
its to start: a SaaS API, a service another team runs, a database behind a VPN.
What it *can* start is an application that runs on this machine and ships with
MORPH — the three bundled demonstration services. ``up`` starts those, leaves
everything else alone, and reports which is which, so the command is never
mistaken for a general process manager.

The second thing it removes is the token dance. The demo applications generate a
bearer token each and persist it; MORPH reads that token from the environment
variable the connector names. Outside this command those two facts have to be
joined by hand — run the applications, then append the tokens to the Hermes
profile environment. ``up`` does it, idempotently, and says so.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from morph.config import settings

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: What ``state`` on a :class:`Plan` means.
RUNNING = "running"        # already answering its health endpoint
STARTABLE = "startable"    # bundled with MORPH, on loopback, not answering yet
EXTERNAL = "external"      # reachable or not, it is not ours to start
DISABLED = "disabled"      # turned off in configuration


@dataclass(frozen=True)
class Plan:
    """One configured connector, and what ``up`` can do about it."""

    connector_id: str
    label: str
    base_url: str
    state: str
    detail: str = ""
    service: str = ""      # the bundled application's module name, when startable
    token_env: str = ""

    @property
    def actionable(self) -> bool:
        return self.state == STARTABLE


def bundled_specs() -> dict[str, Any]:
    """The demonstration applications, keyed by id — empty when they are absent.

    The plugin is installable without them: ``apps`` is the demo, not the product.
    A missing ``apps`` package therefore means "nothing here is mine to start",
    never an error.
    """
    try:
        from apps import serve as app_serve
    except Exception:  # pragma: no cover - exercised by test_up_without_bundled_apps
        return {}
    found: dict[str, Any] = {}
    for name in app_serve.SERVICES:
        try:
            spec = app_serve.spec_of(name)
        except Exception:
            continue
        found[spec.id] = (name, spec)
    return found


def _is_loopback(base_url: str) -> tuple[bool, int]:
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False, 0
    return host in LOOPBACK, port


def survey(probes: Optional[list[dict[str, Any]]] = None) -> list[Plan]:
    """Classify every configured connector without starting anything."""
    if probes is None:
        from morph import registry
        probes = registry.health()
    by_id = {str(p.get("id")): p for p in probes}
    specs = bundled_specs()

    plans: list[Plan] = []
    for config in settings().connectors:
        probe = by_id.get(config.id) or {}
        if not config.enabled:
            plans.append(Plan(config.id, config.label, config.base_url, DISABLED,
                              "disabled in configuration"))
            continue
        if probe.get("ok"):
            entities = ", ".join(probe.get("entities") or [])
            plans.append(Plan(config.id, config.label, config.base_url, RUNNING,
                              entities, token_env=config.token_env))
            continue

        loopback, port = _is_loopback(config.base_url)
        entry = specs.get(config.id)
        if entry is not None and loopback and entry[1].port == port:
            plans.append(Plan(config.id, config.label, config.base_url, STARTABLE,
                              "bundled with MORPH", service=entry[0],
                              token_env=config.token_env))
            continue

        why = str(probe.get("error") or "not reachable")
        if entry is not None and not loopback:
            why = f"configured at {config.base_url}, which is not this machine"
        elif entry is not None:
            why = (f"configured on port {port}, but the bundled "
                   f"{config.id} serves {entry[1].port}")
        plans.append(Plan(config.id, config.label, config.base_url, EXTERNAL, why,
                          token_env=config.token_env))
    return plans


def token_exports(plans: list[Plan]) -> dict[str, str]:
    """``{VAR: token}`` for the bundled applications among *plans*.

    The tokens are read through the applications' own accessor, which persists one
    per service, so repeated calls return the same values rather than rotating a
    credential out from under a running process.
    """
    specs = bundled_specs()
    try:
        from apps._runtime import token_for
    except Exception:  # pragma: no cover - no bundled apps
        return {}
    out: dict[str, str] = {}
    for plan in plans:
        entry = specs.get(plan.connector_id)
        if entry is None or not plan.token_env:
            continue
        out[plan.token_env] = token_for(entry[1])
    return out


def prime_environment() -> dict[str, str]:
    """Load the bundled applications' tokens into this process's environment.

    This has to happen *before* anything probes health, because a probe without a
    credential reports the application as unreachable — indistinguishable, to a
    caller, from one that is not running. Surveying first and priming afterwards
    makes ``up`` believe a live application is dead and start a second copy of it
    on a port that is already bound.

    Returns only what it installed. A variable the operator already exported is
    left alone *and* left out, because the caller writes what comes back to the
    Hermes profile file — and a secret someone chose to keep in their shell should
    not be persisted to disk by a command they ran for another reason.
    """
    specs = bundled_specs()
    try:
        from apps._runtime import token_for
    except Exception:  # pragma: no cover - no bundled apps
        return {}
    loaded: dict[str, str] = {}
    for config in settings().connectors:
        entry = specs.get(config.id)
        if entry is None or not config.token_env:
            continue
        if os.environ.get(config.token_env, "").strip():
            continue
        value = token_for(entry[1])
        os.environ[config.token_env] = value
        loaded[config.token_env] = value
    return loaded


def port_is_free(host: str, port: int) -> bool:
    """True when nothing is listening on *port*, so starting there is safe."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.75)
        try:
            return probe.connect_ex((host, port)) != 0
        except OSError:  # pragma: no cover - unresolvable host
            return False


def hermes_env_path() -> Path:
    home = os.environ.get("HERMES_HOME") or "~/.hermes"
    return Path(home).expanduser() / ".env"


def ensure_profile_env(exports: dict[str, str], path: Optional[Path] = None) -> list[str]:
    """Add any missing ``VAR=token`` line to the Hermes profile environment.

    Returns the names actually written. A variable already present is left exactly
    as it is: the user may have pointed MORPH at their own service with the same
    variable name, and silently overwriting that would be a surprise at best.
    """
    if not exports:
        return []
    target = path or hermes_env_path()
    existing = ""
    if target.exists():
        existing = target.read_text(encoding="utf-8")
    present = {line.split("=", 1)[0].strip()
               for line in existing.splitlines() if "=" in line and not line.startswith("#")}
    missing = {name: value for name, value in exports.items() if name not in present}
    if not missing:
        return []

    target.parent.mkdir(parents=True, exist_ok=True)
    block = "".join(f"{name}={value}\n" for name, value in sorted(missing.items()))
    prefix = "" if (not existing or existing.endswith("\n")) else "\n"
    with target.open("a", encoding="utf-8") as handle:
        if not existing:
            handle.write("# Added by `hermes morph up` — bearer tokens for the "
                         "bundled demonstration applications.\n")
        handle.write(prefix + block)
    try:
        target.chmod(0o600)
    except OSError:  # pragma: no cover - filesystem without chmod
        pass
    return sorted(missing)


def start(plans: list[Plan], host: str = "127.0.0.1",
          timeout: float = 25.0) -> tuple[list[Any], list[str]]:
    """Spawn the startable applications and wait for them to answer.

    Returns the child processes and the ids that did not become ready in time.
    """
    startable = [plan for plan in plans if plan.actionable]
    if not startable:
        return [], []
    from apps import serve as app_serve

    prime_environment()                  # children inherit this process's env
    children = []
    occupied: list[str] = []
    for plan in startable:
        _loopback, port = _is_loopback(plan.base_url)
        if not port_is_free(host, port):
            # Something already holds the port. Starting a second copy would die
            # on bind and leave a corpse, so treat it as somebody else's.
            occupied.append(plan.connector_id)
            continue
        children.append((plan.connector_id, app_serve._child(plan.service, host)))
    startable = [plan for plan in startable if plan.connector_id not in occupied]
    if not children:
        return [], []
    names = [plan.service for plan in startable]
    ready = app_serve.wait_ready(names, host, timeout=timeout)
    if ready:
        return [child for _id, child in children], []
    return [child for _id, child in children], [plan.connector_id for plan in startable]


def stop(children: list[Any]) -> None:
    """Ask every child to exit, then wait briefly for it."""
    import signal
    for child in children:
        if child.poll() is None:
            try:
                child.send_signal(signal.SIGTERM)
            except OSError:  # pragma: no cover - already gone
                pass
    for child in children:
        try:
            child.wait(timeout=10)
        except Exception:  # pragma: no cover - refuses to die
            try:
                child.kill()
            except OSError:
                pass
