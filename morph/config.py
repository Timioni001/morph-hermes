"""Configuration, secret access and network policy for MORPH.

Precedence, highest first:

1. ``MORPH_CONFIG`` — inline JSON (handy for tests and CI).
2. ``MORPH_CONFIG_FILE`` — path to a JSON file.
3. Hermes plugin config (``ctx.get_config("morph.config")``), when MORPH is
   registered inside a Hermes agent.
4. ``<plugin data dir>/config.json`` — the operator-editable copy.
5. ``config/connectors.default.json`` shipped with the plugin (the demo wiring).

Secrets are never part of configuration.  A connector names an environment
variable and the token is read through Hermes's own profile-aware
``agent.secret_scope.get_secret`` when available, so the active profile's value
wins; outside Hermes it falls back to the process environment.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from morph.errors import ConfigError
from morph.util import ENV_NAME, IDENT, RISK_ORDER, clean_text, is_ref

PLUGIN_NAME = "morph"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "connectors.default.json"

# Set by ``register(ctx)`` so configuration can come from Hermes when hosted there.
_host_config_reader: Optional[Callable[[str, Any], Any]] = None


def bind_host_config(reader: Optional[Callable[[str, Any], Any]]) -> None:
    global _host_config_reader
    _host_config_reader = reader
    settings.cache_clear()


def data_dir() -> Path:
    """Where MORPH keeps its own state.  Never holds business data: only
    workspaces, proposals and the audit log."""
    override = os.environ.get("MORPH_DATA_DIR", "").strip()
    if override:
        directory = Path(override).expanduser()
    else:
        try:  # published plugin API: honours the active Hermes profile
            from plugins.plugin_storage import plugin_data_dir
            directory = Path(plugin_data_dir(PLUGIN_NAME))
        except Exception:  # noqa: BLE001 - running outside Hermes
            home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
            directory = Path(home).expanduser() / "plugin-data" / PLUGIN_NAME
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def get_secret(name: str) -> str:
    """Read a credential by environment-variable name through Hermes when hosted.

    The active Hermes profile's secret scope first, then this process's
    environment when that is empty or unavailable. MORPH never reads ``.env``,
    ``auth.json`` or any other credential store itself: catalog rule 11 reserves
    that to Hermes, because a direct read can pick up another profile's key under
    multiplexing.
    """
    if not ENV_NAME.match(name or ""):
        raise ConfigError(f"{name!r} is not a valid environment variable name")
    value = ""
    try:
        from agent.secret_scope import get_secret as hermes_secret  # published plugin API
        value = hermes_secret(name, "") or ""
    except Exception:  # noqa: BLE001 - outside Hermes, or no scope bound
        value = ""
    return value.strip() or os.environ.get(name, "").strip()


# ─────────────────────────────── network policy ────────────────────────────────

def _is_private(host: str) -> bool:
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise ConfigError(f"connector host {host!r} does not resolve") from exc
    if not addresses:
        raise ConfigError(f"connector host {host!r} does not resolve")
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if not (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def is_loopback_host(host: str) -> bool:
    """Whether every address *host* resolves to is a loopback address."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    return bool(addresses) and all(ipaddress.ip_address(a).is_loopback for a in addresses)


def assert_url_allowed(url: str, *, allow_loopback: bool, allowed_hosts: tuple[str, ...] = ()) -> str:
    """Refuse any base URL MORPH is not allowed to talk to.

    The rules, in order: a parseable http(s) URL with no credentials, query or
    fragment; the host on the operator's allowlist when one is configured; HTTPS
    everywhere except an explicitly enabled loopback address (which is how the
    bundled demo services are reached); and no private, link-local, reserved or
    cloud-metadata target otherwise.  Hermes's own ``tools.url_safety.is_safe_url``
    is consulted as well when MORPH runs inside an agent, so a MORPH connector can
    never reach somewhere the host would refuse.
    """
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https"):
        raise ConfigError("connector base_url must be http(s)")
    if parts.username or parts.password:
        raise ConfigError("connector base_url must not embed credentials")
    if parts.query or parts.fragment:
        raise ConfigError("connector base_url must not carry a query or fragment")
    host = parts.hostname
    if not host:
        raise ConfigError("connector base_url has no host")
    host = host.lower()
    if allowed_hosts and host not in allowed_hosts:
        raise ConfigError(f"connector host {host!r} is not on the configured allowlist")
    if host in ("metadata.google.internal", "metadata", "instance-data"):
        raise ConfigError("connector host is a cloud metadata endpoint")
    loopback = is_loopback_host(host)
    if parts.scheme == "http":
        if not (loopback and allow_loopback):
            raise ConfigError(
                "plain HTTP is only allowed for a loopback connector with allow_loopback enabled")
    if not loopback:
        if _is_private(host):
            raise ConfigError(f"connector host {host!r} resolves to a private or reserved address")
        try:
            from tools.url_safety import is_safe_url  # published plugin API
        except Exception:  # noqa: BLE001 - not running inside Hermes
            pass
        else:
            if not is_safe_url(url):
                raise ConfigError(f"the Hermes URL safety policy refuses {host!r}")
    elif not allow_loopback:
        raise ConfigError("loopback connectors require allow_loopback: true")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


# ───────────────────────────────── dataclasses ─────────────────────────────────

@dataclass(frozen=True)
class ConnectorConfig:
    id: str
    kind: str
    label: str
    base_url: str = ""
    token_env: str = ""
    timeout: float = 8.0
    enabled: bool = True
    verify_tls: bool = True
    max_records: int = 200
    # A read-only connector is refused at the connector edge, before any permission
    # or role question. It is how an operator connects a system they are not willing
    # to let anything write to, including themselves.
    read_only: bool = False
    max_retries: int = 2
    retry_backoff: float = 0.4
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkConfig:
    """A relationship between entities in two *different* applications.

    Neither application can know about the other, so the operator declares the
    join here: ``source`` is ``<app>.<entity>.<field>`` and ``target`` is the
    ``<app>.<entity>.<field>`` it points at.
    """

    name: str
    source: str
    target: str
    cardinality: str = "many"


@dataclass(frozen=True)
class Role:
    id: str
    label: str
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    max_risk: str = "medium"
    can_approve: bool = False


@dataclass(frozen=True)
class Settings:
    connectors: tuple[ConnectorConfig, ...]
    links: tuple[LinkConfig, ...]
    roles: dict[str, Role]
    default_role: str
    user_roles: dict[str, str]
    agent_role: str
    allow_loopback: bool
    allowed_hosts: tuple[str, ...]
    planner_enabled: bool
    planner_max_tokens: int
    proposal_ttl_seconds: float
    source: str

    def connector(self, connector_id: str) -> Optional[ConnectorConfig]:
        return next((c for c in self.connectors if c.id == connector_id), None)

    def role(self, role_id: str) -> Role:
        role = self.roles.get(role_id)
        if role is None:
            raise ConfigError(f"unknown role {role_id!r}")
        return role


# ──────────────────────────────── loading ──────────────────────────────────────

_DEFAULT_ROLES: dict[str, Any] = {
    "operator": {"label": "Operations lead", "allow": ["*"], "deny": [],
                 "max_risk": "medium", "can_approve": True},
    "approver": {"label": "Approver (all risks)", "allow": ["*"], "deny": [],
                 "max_risk": "high", "can_approve": True},
    "analyst": {"label": "Analyst (read only)", "allow": [], "deny": ["*"],
                "max_risk": "low", "can_approve": False},
    "agent": {"label": "Hermes agent", "allow": ["*"], "deny": [],
              "max_risk": "medium", "can_approve": False},
}


def _raw_config() -> tuple[dict[str, Any], str]:
    inline = os.environ.get("MORPH_CONFIG", "").strip()
    if inline:
        return _parse(inline, "MORPH_CONFIG"), "env:MORPH_CONFIG"
    path_env = os.environ.get("MORPH_CONFIG_FILE", "").strip()
    if path_env:
        path = Path(path_env).expanduser()
        if not path.is_file():
            raise ConfigError(f"MORPH_CONFIG_FILE does not exist: {path}")
        return _parse(path.read_text(encoding="utf-8"), str(path)), f"file:{path}"
    if _host_config_reader is not None:
        try:
            hosted = _host_config_reader("config", None)
        except Exception:  # noqa: BLE001 - never let host config break discovery
            hosted = None
        if isinstance(hosted, dict) and hosted:
            return hosted, "hermes:plugins.entries.morph.config"
    local = data_dir() / "config.json"
    if local.is_file():
        return _parse(local.read_text(encoding="utf-8"), str(local)), f"file:{local}"
    if DEFAULT_CONFIG_PATH.is_file():
        return (_parse(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), str(DEFAULT_CONFIG_PATH)),
                "bundled-default")
    return {}, "empty"


def _parse(blob: str, where: str) -> dict[str, Any]:
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{where} is not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError(f"{where} must be a JSON object")
    return parsed


def _connectors(raw: Any, allow_loopback: bool, allowed_hosts: tuple[str, ...]) -> tuple[ConnectorConfig, ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise ConfigError("`connectors` must be a list")
    out: list[ConnectorConfig] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ConfigError("each connector must be an object")
        cid = str(item.get("id", ""))
        if not IDENT.match(cid) or cid != cid.lower():
            raise ConfigError(f"connector id {cid!r} must be lower_snake_case")
        if cid in seen:
            raise ConfigError(f"duplicate connector id {cid!r}")
        seen.add(cid)
        kind = str(item.get("kind") or "http-rest")
        # Imported here, not at module scope: the connectors import this module.
        # Asking the registry rather than repeating its names means a new adapter
        # is usable the moment it is registered, instead of being rejected by a
        # list someone forgot to update.
        from morph.connectors import KINDS
        if kind not in KINDS:
            raise ConfigError(f"connector {cid!r}: unsupported kind {kind!r}; "
                              f"this build has {', '.join(sorted(KINDS))}")
        token_env = str(item.get("token_env") or "")
        if token_env and not ENV_NAME.match(token_env):
            raise ConfigError(f"connector {cid!r}: token_env must be UPPER_SNAKE_CASE")
        base_url = str(item.get("base_url") or "")
        if kind == "http-rest":
            base_url = assert_url_allowed(base_url, allow_loopback=allow_loopback,
                                          allowed_hosts=allowed_hosts)
        timeout = float(item.get("timeout") or 8.0)
        if not 0.5 <= timeout <= 60:
            raise ConfigError(f"connector {cid!r}: timeout must be between 0.5 and 60 seconds")
        max_records = int(item.get("max_records") or 200)
        if not 1 <= max_records <= 2000:
            raise ConfigError(f"connector {cid!r}: max_records must be between 1 and 2000")
        max_retries = int(item.get("max_retries", 2))
        if not 0 <= max_retries <= 5:
            raise ConfigError(f"connector {cid!r}: max_retries must be between 0 and 5")
        retry_backoff = float(item.get("retry_backoff") or 0.4)
        if not 0.05 <= retry_backoff <= 5.0:
            raise ConfigError(f"connector {cid!r}: retry_backoff must be between 0.05 and 5 seconds")
        options = item.get("options") or {}
        if not isinstance(options, dict):
            raise ConfigError(f"connector {cid!r}: options must be an object")
        out.append(ConnectorConfig(
            id=cid, kind=kind, label=clean_text(item.get("label") or cid, 60), base_url=base_url,
            token_env=token_env, timeout=timeout, enabled=bool(item.get("enabled", True)),
            verify_tls=bool(item.get("verify_tls", True)), max_records=max_records,
            read_only=bool(item.get("read_only", False)), max_retries=max_retries,
            retry_backoff=retry_backoff, options=options))
    return tuple(out)


def _links(raw: Any) -> tuple[LinkConfig, ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise ConfigError("`links` must be a list")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise ConfigError("each link must be an object")
        name, src, tgt = str(item.get("name", "")), str(item.get("source", "")), str(item.get("target", ""))
        if not IDENT.match(name):
            raise ConfigError(f"link name {name!r} is invalid")
        if not is_ref(src, 3) or not is_ref(tgt, 3):
            raise ConfigError(f"link {name!r}: source and target must be <app>.<entity>.<field>")
        cardinality = str(item.get("cardinality") or "many")
        if cardinality not in ("one", "many"):
            raise ConfigError(f"link {name!r}: cardinality must be 'one' or 'many'")
        out.append(LinkConfig(name=name, source=src, target=tgt, cardinality=cardinality))
    return tuple(out)


def _roles(raw: Any) -> dict[str, Role]:
    merged = dict(_DEFAULT_ROLES)
    if raw:
        if not isinstance(raw, dict):
            raise ConfigError("`roles` must be an object")
        for key, value in raw.items():
            if not isinstance(value, dict):
                raise ConfigError(f"role {key!r} must be an object")
            merged[str(key)] = {**merged.get(str(key), {}), **value}
    roles: dict[str, Role] = {}
    for rid, spec in merged.items():
        if not IDENT.match(rid):
            raise ConfigError(f"role id {rid!r} is invalid")
        max_risk = str(spec.get("max_risk") or "medium")
        if max_risk not in RISK_ORDER:
            raise ConfigError(f"role {rid!r}: max_risk must be one of {', '.join(RISK_ORDER)}")
        for key in ("allow", "deny"):
            if not isinstance(spec.get(key, []), list):
                raise ConfigError(f"role {rid!r}: {key} must be a list of patterns")
        roles[rid] = Role(
            id=rid, label=clean_text(spec.get("label") or rid, 60),
            allow=tuple(str(p) for p in spec.get("allow", [])),
            deny=tuple(str(p) for p in spec.get("deny", [])),
            max_risk=max_risk, can_approve=bool(spec.get("can_approve", False)))
    # Structural invariant, not a preference: the agent principal may never approve.
    agent = roles.get("agent")
    if agent is not None and agent.can_approve:
        roles["agent"] = Role(**{**agent.__dict__, "can_approve": False})
    return roles


@lru_cache(maxsize=1)
def settings() -> Settings:
    raw, source = _raw_config()
    allow_loopback = bool(raw.get("allow_loopback", False))
    hosts_raw = raw.get("allowed_hosts") or []
    if not isinstance(hosts_raw, list):
        raise ConfigError("`allowed_hosts` must be a list of host names")
    allowed_hosts = tuple(str(h).strip().lower() for h in hosts_raw if str(h).strip())
    roles = _roles(raw.get("roles"))
    users_raw = raw.get("user_roles") or {}
    if not isinstance(users_raw, dict):
        raise ConfigError("`user_roles` must be an object of identity -> role id")
    user_roles = {str(k).strip().lower(): str(v) for k, v in users_raw.items()}
    default_role = str(raw.get("default_role") or "operator")
    agent_role = str(raw.get("agent_role") or "agent")
    for rid in {default_role, agent_role, *user_roles.values()}:
        if rid not in roles:
            raise ConfigError(f"role {rid!r} is referenced but not defined")
    ttl = float(raw.get("proposal_ttl_seconds") or 3600)
    if not 60 <= ttl <= 86400:
        raise ConfigError("proposal_ttl_seconds must be between 60 and 86400")
    return Settings(
        connectors=_connectors(raw.get("connectors"), allow_loopback, allowed_hosts),
        links=_links(raw.get("links")),
        roles=roles,
        default_role=default_role,
        user_roles=user_roles,
        agent_role=agent_role,
        allow_loopback=allow_loopback,
        allowed_hosts=allowed_hosts,
        planner_enabled=bool(raw.get("planner_enabled", True)),
        planner_max_tokens=int(raw.get("planner_max_tokens") or 2600),
        proposal_ttl_seconds=ttl,
        source=source,
    )


def reload_settings() -> Settings:
    settings.cache_clear()
    return settings()
