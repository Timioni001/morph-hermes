"""``hermes morph connect`` — wire an API to MORPH without editing JSON by hand.

An unconfigured MORPH is connected to nothing. These functions are how that
changes: each one adds (or removes) exactly one connection in
``<plugin data dir>/config.json``, proves the result against the live service,
and rolls the file back if the service does not answer.

Three things can be connected:

* **github** — one repository's issues and pull requests. Works anonymously
  (read-only) for a public repository; a token makes it writable.
* **openapi** — any service that publishes an OpenAPI 3 document.
* **sandbox** — three practice applications bundled with MORPH, for trying the
  approval flow offline before pointing it at something real.

Credentials are never written here. A connection records the *name* of the
environment variable that holds its token; Hermes owns the value.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from morph import config
from morph.errors import ConfigError

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
GITHUB_HOST = "api.github.com"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"


def config_path() -> Path:
    return config.data_dir() / "config.json"


def _editable_source() -> None:
    """Refuse to edit a file that would not be the one MORPH reads.

    Configuration given inline, by path or through Hermes's own plugin config
    outranks the data-directory file. Writing the file anyway would report
    success and change nothing.
    """
    source = config.settings().source
    if source == "empty" or source == f"file:{config_path()}":
        return
    raise ConfigError(
        f"MORPH is configured from {source}, which outranks the file "
        f"`connect` edits. Change it there, or remove it to manage connections "
        f"with this command.")


def _load() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {"connectors": [], "links": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path} is not valid JSON; fix or remove it first") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must hold a JSON object")
    raw.setdefault("connectors", [])
    raw.setdefault("links", [])
    return raw


def _write(raw: dict[str, Any]) -> None:
    path = config_path()
    scratch = path.with_suffix(".json.tmp")
    scratch.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(scratch, 0o600)
    os.replace(scratch, path)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"api_{slug}".strip("_")
    return slug[:32]


def _allow(raw: dict[str, Any], host: str) -> None:
    hosts = [str(h) for h in raw.get("allowed_hosts") or []]
    if host not in hosts:
        hosts.append(host)
    raw["allowed_hosts"] = hosts
    if host in LOOPBACK:
        raw["allow_loopback"] = True


def _commit(raw: dict[str, Any], probe_ids: list[str], *, keep_unreachable: bool) -> dict[str, Any]:
    """Write *raw*, reload, probe the new connections; undo it all if they fail."""
    from morph import engine, registry

    path = config_path()
    previous = path.read_text(encoding="utf-8") if path.is_file() else None

    def restore() -> None:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(previous, encoding="utf-8")
        engine.reload()

    _write(raw)
    try:
        engine.reload()
    except Exception:
        restore()
        raise
    probes = [p for p in registry.health() if p.get("id") in probe_ids]
    failed = [p for p in probes if not p.get("ok")]
    if failed and not keep_unreachable:
        restore()
        detail = "; ".join(f"{p['id']}: {p.get('error') or 'not reachable'}" for p in failed)
        raise ConfigError(f"not connected — {detail}. Nothing was changed. "
                          f"(--keep saves the connection anyway.)")
    return {"connected": probe_ids, "probes": probes, "config": str(path)}


def _add(raw: dict[str, Any], entry: dict[str, Any], *, replace: bool) -> None:
    existing = [c for c in raw["connectors"] if c.get("id") == entry["id"]]
    if existing and not replace:
        raise ConfigError(f"a connection called {entry['id']!r} already exists; "
                          f"pass --id to add a second one or --replace to overwrite it")
    raw["connectors"] = [c for c in raw["connectors"] if c.get("id") != entry["id"]] + [entry]


# ─────────────────────────────── the three kinds ───────────────────────────────

def add_github(repo: str, *, connector_id: str = "", token_env: str = GITHUB_TOKEN_ENV,
               read_only: bool = False, replace: bool = False,
               keep_unreachable: bool = False) -> dict[str, Any]:
    """Connect one GitHub repository's issues and pull requests."""
    from morph.connectors.github import REPO_RE

    repo = repo.strip().removeprefix("https://github.com/").strip("/")
    if not REPO_RE.match(repo):
        raise ConfigError(f"{repo!r} is not a repository; write it as owner/name")
    cid = connector_id or _slug(repo.split("/", 1)[1])
    if not ID_RE.match(cid):
        raise ConfigError(f"--id {cid!r} must be lower_snake_case")
    _editable_source()
    raw = _load()
    _add(raw, {"id": cid, "kind": "github", "label": repo, "token_env": token_env,
               "read_only": read_only, "timeout": 12.0, "options": {"repo": repo}},
         replace=replace)
    _allow(raw, GITHUB_HOST)
    return _commit(raw, [cid], keep_unreachable=keep_unreachable)


def add_openapi(base_url: str, *, connector_id: str = "", spec_path: str = "/openapi.json",
                token_env: str = "", label: str = "", read_only: bool = False,
                replace: bool = False, keep_unreachable: bool = False) -> dict[str, Any]:
    """Connect any service that publishes an OpenAPI 3 document."""
    parts = urlsplit(base_url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise ConfigError(f"{base_url!r} is not an absolute http(s) URL")
    cid = connector_id or _slug(host.removeprefix("api.").removeprefix("www.").split(".")[0])
    if not ID_RE.match(cid):
        raise ConfigError(f"--id {cid!r} must be lower_snake_case")
    _editable_source()
    raw = _load()
    _add(raw, {"id": cid, "kind": "openapi", "label": label or host,
               "base_url": base_url.strip().rstrip("/"), "token_env": token_env,
               "read_only": read_only, "timeout": 10.0,
               "options": {"spec_path": spec_path}}, replace=replace)
    _allow(raw, host)
    return _commit(raw, [cid], keep_unreachable=keep_unreachable)


def add_sandbox(*, replace: bool = False) -> dict[str, Any]:
    """Install the three bundled practice applications and the joins between them.

    They are not probed: `hermes morph up` starts them, and until it has they are
    legitimately unreachable.
    """
    try:
        sandbox = json.loads(config.SANDBOX_CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError("this install does not include the sandbox applications") from exc
    _editable_source()
    raw = _load()
    for entry in sandbox["connectors"]:
        _add(raw, entry, replace=replace)
    names = {link.get("name") for link in raw["links"]}
    raw["links"] += [link for link in sandbox.get("links", []) if link.get("name") not in names]
    # The sandbox operator is denied one operation on purpose, so the practice
    # run shows a refusal. Only installed when no roles were defined already.
    if sandbox.get("roles") and not raw.get("roles"):
        raw["roles"] = sandbox["roles"]
    for host in sandbox.get("allowed_hosts", []):
        _allow(raw, host)
    ids = [entry["id"] for entry in sandbox["connectors"]]
    result = _commit(raw, ids, keep_unreachable=True)
    result["next"] = "hermes morph up"
    return result


def remove(connector_id: str) -> dict[str, Any]:
    """Disconnect one application, and any join that referred to it."""
    from morph import engine

    _editable_source()
    raw = _load()
    if not any(c.get("id") == connector_id for c in raw["connectors"]):
        known = ", ".join(sorted(str(c.get("id")) for c in raw["connectors"])) or "nothing"
        raise ConfigError(f"no connection called {connector_id!r}; connected: {known}")
    raw["connectors"] = [c for c in raw["connectors"] if c.get("id") != connector_id]
    prefix = f"{connector_id}."
    dropped = [link for link in raw["links"]
               if str(link.get("source", "")).startswith(prefix)
               or str(link.get("target", "")).startswith(prefix)]
    raw["links"] = [link for link in raw["links"] if link not in dropped]
    _write(raw)
    engine.reload()
    return {"disconnected": connector_id,
            "links_removed": [str(link.get("name")) for link in dropped]}


def summary(connector_id: Optional[str] = None) -> list[dict[str, Any]]:
    """What each connection offers: entities, and the operations it could propose."""
    from morph import registry

    out = []
    for probe in registry.health():
        if connector_id and probe.get("id") != connector_id:
            continue
        entry: dict[str, Any] = {"id": probe.get("id"), "ok": bool(probe.get("ok")),
                                 "label": probe.get("label") or probe.get("id"),
                                 "error": probe.get("error") or "", "entities": [],
                                 "acting_as": probe.get("acting_as") or ""}
        if probe.get("ok"):
            described = registry.get(str(probe["id"])).describe()
            for entity in described.entities:
                entry["entities"].append({
                    "name": entity.name,
                    "operations": [op.id for op in entity.operations]})
        out.append(entry)
    return out
