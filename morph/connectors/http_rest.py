"""HTTP/REST connector.

Reaches an application that publishes a ``morph.source.v1`` schema document.  The
bundled demo applications speak it; so can any service that adds three endpoints
(see ``docs/CONNECTORS.md``):

* ``GET  {schema_path}``                        → the schema document
* ``GET  {records_path}/<entity>``              → ``{"items": [...], "total": n}``
* ``POST {records_path}/<entity>/<id>/operations/<op>`` → apply one operation

Security posture: the base URL is re-validated against the network policy before
every request (DNS answers change), redirects are never followed, responses are
size-capped, the bearer token is read through Hermes's secret scope at call time
and never logged, and filters applied by the source are re-applied locally so a
misbehaving source cannot widen a result set.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional, Sequence
from urllib.parse import urlsplit

from morph.config import (
    ConnectorConfig, assert_url_allowed, get_secret, is_loopback_host, settings,
)
from morph.connectors import netguard
from morph.connectors.base import record_segment
from morph.connectors.base import (
    ApplyResult, Connector, FetchResult, Predicate, SourceDescriptor,
)
from morph.errors import ConfigError, ConnectorError, NotFound, PermissionDenied, StaleRecord
from morph.util import clean_text

logger = logging.getLogger("morph.connector")

MAX_RESPONSE_BYTES = 2_000_000
SCHEMA_TTL_SECONDS = 120.0


def _local_match(record: dict[str, Any], predicate: Predicate) -> bool:
    have, want, op = record.get(predicate.field), predicate.value, predicate.op
    if op == "exists":
        return (have is not None) == bool(want)
    if have is None:
        return op in ("neq", "nin")
    try:
        if op == "eq":
            return have == want
        if op == "neq":
            return have != want
        if op == "in":
            return isinstance(want, (list, tuple)) and have in want
        if op == "nin":
            return isinstance(want, (list, tuple)) and have not in want
        if op == "contains":
            return str(want).lower() in str(have).lower()
        if op == "gt":
            return have > want
        if op == "gte":
            return have >= want
        if op == "lt":
            return have < want
        if op == "lte":
            return have <= want
    except TypeError:
        return False
    return False


class HttpRestConnector(Connector):
    kind = "http-rest"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._lock = threading.RLock()
        self._client: Any = None
        self._schema: Optional[SourceDescriptor] = None
        self._guarded = False
        self._schema_at = 0.0
        options = config.options or {}
        self._schema_path = str(options.get("schema_path") or "/morph/schema")
        self._records_path = str(options.get("records_path") or "/v1")
        self._push_filters = str(options.get("filter_style") or "morph") == "morph"
        self._changes_path = str(options.get("changes_path", "/v1/_changes") or "")

    # ───────────────────────────── transport ──────────────────────────────

    def _checked_base(self) -> str:
        current = settings()
        return assert_url_allowed(self.config.base_url, allow_loopback=current.allow_loopback,
                                  allowed_hosts=current.allowed_hosts)

    def _get_client(self) -> Any:
        """Build the HTTP client, once.

        A connect-time guard (``morph.connectors.netguard``) validates the address
        the socket actually dials, which is what closes the DNS-rebinding window
        that pre-request URL validation leaves open.  Loopback is dialled only for a
        connector the operator deliberately pointed at it (``allow_loopback: true``
        — how the bundled demo applications are reached); the narrowing is per
        connector and never relaxes anything for the rest of Hermes.
        """
        with self._lock:
            if self._client is None:
                import httpx
                loopback = self._loopback()
                client = httpx.Client(
                    timeout=self.config.timeout, follow_redirects=False,
                    verify=self.config.verify_tls,
                    headers={"Accept": "application/json",
                             "User-Agent": "morph-connector/1.0"})
                self._guarded = netguard.install(client, allow_loopback=loopback)
                if not self._guarded:
                    logger.warning(
                        "MORPH could not attach its connect-time SSRF guard for %r; falling back "
                        "to validating the URL before every request", self.id)
                self._client = client
            return self._client

    def _loopback(self) -> bool:
        host = (urlsplit(self.config.base_url).hostname or "").lower()
        return bool(host) and is_loopback_host(host)

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None

    def _auth_headers(self) -> dict[str, str]:
        if not self.config.token_env:
            return {}
        token = get_secret(self.config.token_env)
        if not token:
            raise ConnectorError(
                f"{self.id!r} needs a credential: set {self.config.token_env} in the Hermes profile "
                f"environment (see .env.example)")
        return {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, path: str, *, params: Any = None, body: Any = None,
                 headers: Optional[dict[str, str]] = None) -> tuple[int, Any]:
        base = self._checked_base()
        url = f"{base}{path}"
        merged = {**self._auth_headers(), **(headers or {})}
        client = self._get_client()
        response = self._send_with_retries(client, method, url, params, body, merged)
        if response.status_code in (301, 302, 303, 307, 308):
            raise ConnectorError(f"{self.id!r} answered with a redirect, which MORPH never follows")
        raw = response.content or b""
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ConnectorError(f"{self.id!r} returned more than {MAX_RESPONSE_BYTES} bytes")
        if not raw:
            return response.status_code, None
        try:
            return response.status_code, json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorError(f"{self.id!r} returned a body that is not JSON") from exc

    def _send_with_retries(self, client: Any, method: str, url: str, params: Any,
                           body: Any, headers: dict[str, str]) -> Any:
        """Send one request, retrying only what is safe to retry.

        A read may be retried freely. A write may not — a timeout does not tell you
        whether the source applied it — except that MORPH gives every write an
        idempotency key, which is exactly what makes one retry safe. Beyond that:
        5xx and transport failures back off; 429 waits for ``Retry-After`` when the
        source names one; 4xx is the source's answer and is never retried.
        """
        idempotent = method.upper() == "GET" or "Idempotency-Key" in headers
        attempts = self.config.max_retries + 1 if idempotent else 1
        last: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                response = client.request(method, url, params=params, json=body, headers=headers)
            except Exception as exc:  # noqa: BLE001 - any transport failure is an outage
                last = exc
                if attempt + 1 >= attempts:
                    raise ConnectorError(
                        f"{self.id!r} is unreachable after {attempt + 1} attempt(s): "
                        f"{type(exc).__name__}") from exc
                time.sleep(self._backoff(attempt))
                continue
            if response.status_code == 429 and attempt + 1 < attempts:
                time.sleep(self._retry_after(response, attempt))
                continue
            if 500 <= response.status_code < 600 and attempt + 1 < attempts:
                time.sleep(self._backoff(attempt))
                continue
            return response
        raise ConnectorError(f"{self.id!r} is unreachable: {type(last).__name__ if last else 'unknown'}")

    def _backoff(self, attempt: int) -> float:
        """Exponential with jitter, capped — and capped again by the request timeout,
        so a retry budget can never outlast the page load waiting on it."""
        base = min(self.config.retry_backoff * (2 ** attempt), 4.0)
        return base * (0.6 + 0.4 * (hash((self.id, attempt)) % 100) / 100.0)

    def _retry_after(self, response: Any, attempt: int) -> float:
        raw = ""
        try:
            raw = response.headers.get("retry-after", "") or ""
        except Exception:  # noqa: BLE001 - a header we cannot read is a header we ignore
            raw = ""
        try:
            return max(0.0, min(float(raw), 10.0))
        except (TypeError, ValueError):
            return self._backoff(attempt)

    @staticmethod
    def _detail(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict):
            for key in ("detail", "message", "error"):
                if payload.get(key):
                    return clean_text(payload[key], 200)
        return fallback

    # ───────────────────────────── discovery ──────────────────────────────

    def describe(self, *, refresh: bool = False) -> SourceDescriptor:
        with self._lock:
            fresh = self._schema is not None and (time.time() - self._schema_at) < SCHEMA_TTL_SECONDS
            if fresh and not refresh:
                return self._schema  # type: ignore[return-value]
        status, payload = self._request("GET", self._schema_path)
        if status == 401 or status == 403:
            raise ConnectorError(f"{self.id!r} rejected MORPH's credential (HTTP {status})")
        if status != 200:
            raise ConnectorError(f"{self.id!r} schema endpoint returned HTTP {status}")
        described = SourceDescriptor.parse(payload, connector_id=self.id, kind=self.kind,
                                           fetched_at=time.time())
        with self._lock:
            self._schema, self._schema_at = described, time.time()
        return described

    def health(self) -> dict[str, Any]:
        started = time.time()
        try:
            described = self.describe()
        except (ConnectorError, ConfigError, Exception) as exc:  # noqa: BLE001 - must never raise
            message = exc.message if hasattr(exc, "message") else f"{type(exc).__name__}"
            return {"id": self.id, "kind": self.kind, "ok": False, "error": clean_text(message, 200),
                    "latency_ms": round((time.time() - started) * 1000, 1)}
        return {"id": self.id, "kind": self.kind, "ok": True, "label": described.label,
                "entities": [e.name for e in described.entities],
                "connect_guard": self._guarded,
                "latency_ms": round((time.time() - started) * 1000, 1)}

    # ─────────────────────────────── reads ────────────────────────────────

    def fetch(self, entity: str, *, filters: Sequence[Predicate] = (), limit: int = 100,
              sort: Optional[tuple[str, str]] = None) -> FetchResult:
        described = self.entity_or_raise(entity)
        limit = max(1, min(int(limit), described.max_page_size, self.config.max_records))
        params: list[tuple[str, str]] = [("limit", str(limit))]
        pushed: list[Predicate] = []
        if self._push_filters:
            for predicate in filters:
                if predicate.op not in described.filter_ops:
                    continue
                if predicate.op in ("in", "nin"):
                    if not isinstance(predicate.value, (list, tuple)):
                        continue
                    value = ",".join(str(v) for v in predicate.value)
                else:
                    value = "" if predicate.value is None else str(predicate.value)
                key = predicate.field if predicate.op == "eq" else f"{predicate.field}__{predicate.op}"
                params.append((key, value))
                pushed.append(predicate)
        if sort and described.sortable:
            params.append(("sort", f"{sort[0]}:{sort[1]}"))
        status, payload = self._request("GET", f"{self._records_path}/{entity}", params=params)
        if status == 404:
            raise ConnectorError(f"{self.id!r} has no entity {entity!r}")
        if status != 200:
            raise ConnectorError(f"{self.id!r} returned HTTP {status} reading {entity!r}: "
                                 f"{self._detail(payload, 'no detail')}")
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ConnectorError(f"{self.id!r} returned no item list for {entity!r}")
        records = [r for r in items if isinstance(r, dict)]
        # Re-apply every predicate locally: the source's filtering is an optimisation,
        # never the authority for what the operator asked to see.
        for predicate in filters:
            records = [r for r in records if _local_match(r, predicate)]
        if sort:
            field, direction = sort
            records.sort(key=lambda r: (r.get(field) is None, _sort_key(r.get(field))),
                         reverse=direction == "desc")
        total = payload.get("total") if isinstance(payload, dict) else None
        truncated = len(records) >= limit
        return FetchResult(records=records[:limit],
                           total=int(total) if isinstance(total, int) else len(records),
                           truncated=truncated)

    def get(self, entity: str, record_id: str) -> Optional[dict[str, Any]]:
        self.entity_or_raise(entity)
        status, payload = self._request("GET", f"{self._records_path}/{entity}/{record_segment(record_id)}")
        if status == 404:
            return None
        if status != 200 or not isinstance(payload, dict):
            raise ConnectorError(f"{self.id!r} returned HTTP {status} reading {entity}/{record_id}")
        return payload

    def changes(self, since: int = 0) -> dict[str, Any]:
        """Read the source's own change feed, when it publishes one."""
        if not self._changes_path:
            return {"supported": False, "cursor": since, "changes": []}
        try:
            status, payload = self._request("GET", self._changes_path,
                                            params=[("since", str(int(since)))])
        except ConnectorError:
            return {"supported": False, "cursor": since, "changes": [],
                    "error": "the change feed could not be read"}
        if status == 404:
            return {"supported": False, "cursor": since, "changes": []}
        if status != 200 or not isinstance(payload, dict):
            return {"supported": False, "cursor": since, "changes": [],
                    "error": f"the change feed returned HTTP {status}"}
        raw = payload.get("changes")
        entries = [
            {"seq": int(c.get("seq") or 0), "entity": str(c.get("entity") or ""),
             "record_id": str(c.get("record_id") or ""),
             "operation": clean_text(c.get("operation"), 48),
             "actor": clean_text(c.get("actor"), 60), "at": c.get("at")}
            for c in (raw if isinstance(raw, list) else [])[:500] if isinstance(c, dict)]
        return {"supported": True, "cursor": int(payload.get("cursor") or since),
                "has_more": bool(payload.get("has_more")), "changes": entries}

    # ─────────────────────────────── writes ───────────────────────────────

    def apply(self, entity: str, record_id: str, operation: str, params: dict[str, Any], *,
              expected_version: Optional[int], idempotency_key: str, actor: str) -> ApplyResult:
        if self.config.read_only:
            raise PermissionDenied(
                f"connector {self.id!r} is configured read-only; no write reaches it")
        described = self.entity_or_raise(entity)
        if described.operation(operation) is None:
            raise ConnectorError(f"{self.id}.{entity} does not expose operation {operation!r}")
        headers = {"Idempotency-Key": idempotency_key[:120], "X-Actor": clean_text(actor, 60)}
        if expected_version is not None:
            headers["If-Match"] = str(expected_version)
        status, payload = self._request(
            "POST", f"{self._records_path}/{entity}/{record_segment(record_id)}/operations/{operation}",
            body={"params": params}, headers=headers)
        if status == 404:
            raise NotFound(f"{entity}/{record_id} no longer exists in {self.id!r}")
        if status == 409:
            raise StaleRecord(self._detail(payload, "the record changed in the source application"))
        if status in (400, 422):
            raise ConnectorError(f"{self.id!r} refused the operation: "
                                 f"{self._detail(payload, 'invalid parameters')}")
        if status in (401, 403):
            raise ConnectorError(f"{self.id!r} rejected MORPH's credential (HTTP {status})")
        if status != 200 or not isinstance(payload, dict):
            raise ConnectorError(f"{self.id!r} returned HTTP {status} applying {operation!r}")
        record = payload.get("record")
        if not isinstance(record, dict):
            raise ConnectorError(f"{self.id!r} did not return the updated record")
        changed = payload.get("changed")
        return ApplyResult(record=record, changed=changed if isinstance(changed, dict) else {},
                           replayed=bool(payload.get("replayed")))


def _sort_key(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return str(value)
