"""GitHub connector — one repository's issues and pull requests.

This is the connection most people make first: ``hermes morph connect github
owner/name``. With no token it reads a public repository anonymously and offers
no writes; with one, the agent can propose triage — close, reopen, relabel,
retitle, assign — and a person approves each change before it reaches GitHub.

GitHub has no idempotency header, no version field and no change feed in the
shape MORPH asks of a source. What that costs, honestly:

* **Versions** are derived from ``updated_at``. GitHub has no monotonic version, so
  optimistic concurrency here means "the issue has not been touched since the
  proposal was built" — which is the guarantee that matters, reached a different way.
* **Idempotency** is enforced on this side. GitHub accepts no idempotency key, so the
  adapter keeps a replay cache and every operation it offers is set-to-value rather
  than increment-by-one, which makes a repeat harmless even if the cache is lost.
* **Change detection** uses the issues list with ``since``, which is GitHub's own
  incremental read. It catches an issue someone closed in the GitHub UI.

Only operations with a **deterministic, verifiable effect** are offered. Labels
are therefore *set* as a whole list rather than added one at a time: the approver
sees the exact list before and after, and the read-back either matches it or does
not. Posting a comment would be useful and is deliberately absent: its effect is an
increment, so MORPH could not re-read the issue and prove the change it promised.
An action MORPH cannot verify is an action MORPH should not offer.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from morph.config import ConnectorConfig, get_secret
from morph.connectors.base import (
    ApplyResult, Connector, FetchResult, Predicate, SourceDescriptor,
)
from morph.connectors.http_rest import _local_match
from morph.errors import ConfigError, ConnectorError, NotFound, PermissionDenied, StaleRecord
from morph.util import clean_text

ISSUE_NUMBER = re.compile(r"^[1-9][0-9]{0,9}$")


def _issue_number(record_id: Any) -> str:
    """A GitHub issue is addressed by its number and nothing else.

    The id can come from the model; anything that is not a plain number (``../``,
    a slash, an owner/repo) is refused rather than placed in the request path.
    """
    rid = str(record_id if record_id is not None else "").strip()
    if not ISSUE_NUMBER.match(rid):
        raise NotFound(f"{rid[:40]!r} is not a GitHub issue number")
    return rid

API_ROOT = "https://api.github.com"
# Each segment must start with an alphanumeric: "../etc" is a valid-looking
# "owner/name" to a lazier pattern, and it has no business reaching a URL builder.
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
MAX_RESPONSE_BYTES = 4_000_000
SCHEMA_TTL_SECONDS = 300.0
PAGE_SIZE = 100


def _epoch(value: Any) -> int:
    """GitHub timestamps as an integer version. Not monotonic across records, but
    monotonic per record, which is all optimistic concurrency needs."""
    if not value:
        return 0
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                   .astimezone(timezone.utc).timestamp())
    except (TypeError, ValueError):
        return 0


def _split_labels(value: Any) -> list[str]:
    """A label list as people write it — "bug, help wanted" — as distinct names."""
    seen: dict[str, str] = {}
    for part in str(value or "").split(","):
        name = clean_text(part, 50)
        if name:
            seen.setdefault(name.lower(), name)
    return sorted(seen.values(), key=str.lower)


def _join_labels(names: Sequence[str]) -> str:
    return ", ".join(sorted((str(n) for n in names), key=str.lower))[:400]


def _issue_record(raw: dict[str, Any]) -> dict[str, Any]:
    """One GitHub issue, flattened to the typed shape MORPH works in."""
    user = raw.get("user") or {}
    assignee = raw.get("assignee") or {}
    labels = [str(label.get("name")) for label in (raw.get("labels") or [])
              if isinstance(label, dict) and label.get("name")]
    milestone = raw.get("milestone") or {}
    return {
        "number": str(raw.get("number") or ""),
        "title": clean_text(raw.get("title"), 160),
        "state": "closed" if raw.get("state") == "closed" else "open",
        "state_reason": clean_text(raw.get("state_reason"), 24),
        "body": clean_text(raw.get("body"), 400),
        "author": clean_text(user.get("login"), 60),
        "assignee": clean_text(assignee.get("login"), 60),
        "labels": _join_labels(labels),
        "label_count": len(labels),
        "comments": int(raw.get("comments") or 0),
        "milestone": clean_text(milestone.get("title"), 60),
        "is_pull_request": bool(raw.get("pull_request")),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "_version": _epoch(raw.get("updated_at")),
    }


def _descriptor(repo: str, login: str) -> dict[str, Any]:
    """The schema MORPH would have asked GitHub to publish, written here instead."""
    assign_label = f"Assign to {login}" if login else "Assign to me"
    return {
        "label": f"GitHub · {repo}",
        "vendor": "GitHub",
        "description": f"Issues and pull requests in {repo}, read live from the GitHub API.",
        "entities": [{
            "name": "issues",
            "label": "Issues",
            "description": "Open and closed issues, and pull requests, in this repository.",
            "primary_key": "number",
            "title_field": "title",
            "fields": [
                {"name": "number", "type": "id", "label": "#", "role": "key"},
                {"name": "title", "type": "string", "label": "Title", "role": "title"},
                {"name": "state", "type": "enum", "label": "State", "role": "status",
                 "values": ["open", "closed"], "writable": True},
                {"name": "state_reason", "type": "string", "label": "Closed as"},
                {"name": "body", "type": "string", "label": "Description"},
                {"name": "author", "type": "string", "label": "Opened by"},
                {"name": "assignee", "type": "string", "label": "Assignee", "writable": True},
                {"name": "labels", "type": "string", "label": "Labels", "writable": True},
                {"name": "label_count", "type": "int", "label": "Labels", "role": "metric"},
                {"name": "comments", "type": "int", "label": "Comments", "role": "metric"},
                {"name": "milestone", "type": "string", "label": "Milestone"},
                {"name": "is_pull_request", "type": "bool", "label": "Pull request",
                 "role": "flag"},
                {"name": "created_at", "type": "datetime", "label": "Opened",
                 "role": "timestamp"},
                {"name": "updated_at", "type": "datetime", "label": "Updated",
                 "role": "timestamp"},
            ],
            "operations": [
                {"id": "close_issue", "label": "Close issue", "risk": "medium",
                 "description": "Close this issue on GitHub.",
                 "effect": {"state": "closed"},
                 "precondition": [{"field": "state", "op": "neq", "value": "closed",
                                   "message": "the issue is already closed"}]},
                {"id": "close_as_not_planned", "label": "Close as not planned",
                 "risk": "medium",
                 "description": "Close this issue on GitHub, marked 'not planned'.",
                 "effect": {"state": "closed", "state_reason": "not_planned"},
                 "precondition": [{"field": "state", "op": "neq", "value": "closed",
                                   "message": "the issue is already closed"}]},
                {"id": "set_labels", "label": "Set labels", "risk": "low",
                 "description": ("Replace this issue's labels with exactly the list given, "
                                 "comma-separated. Include the labels it should keep."),
                 "params": [{"name": "labels", "type": "string", "label": "Labels",
                             "required": False, "max_length": 400}],
                 "effect": {"labels": "{labels}"}},
                {"id": "set_title", "label": "Retitle", "risk": "low",
                 "description": "Replace this issue's title.",
                 "params": [{"name": "title", "type": "string", "label": "Title",
                             "required": True, "max_length": 160}],
                 "effect": {"title": "{title}"}},
                {"id": "unassign", "label": "Remove assignee", "risk": "low",
                 "description": "Remove every assignee from this issue.",
                 "effect": {"assignee": ""},
                 "precondition": [{"field": "assignee", "op": "neq", "value": "",
                                   "message": "nobody is assigned"}]},
                {"id": "reopen_issue", "label": "Reopen issue", "risk": "low",
                 "description": "Reopen a closed issue on GitHub.",
                 "effect": {"state": "open"},
                 "precondition": [{"field": "state", "op": "eq", "value": "closed",
                                   "message": "the issue is already open"}]},
                {"id": "assign_to_me", "label": assign_label, "risk": "low",
                 "description": "Assign this issue to the account MORPH is authenticated as.",
                 "effect": {"assignee": login},
                 "precondition": [{"field": "assignee", "op": "neq", "value": login,
                                   "message": "already assigned to that account"}]},
            ],
            "capabilities": {
                "read": True, "write": bool(login),
                # Everything GitHub's issues list cannot express is applied locally
                # after the fetch, so the filter vocabulary stays the same as every
                # other connector's.
                "filter_ops": ["eq", "neq", "in", "nin", "gt", "gte", "lt", "lte",
                               "contains", "exists"],
                "sort": True, "paginate": True, "max_page_size": PAGE_SIZE,
                "optimistic_concurrency": "updated_at", "idempotent_writes": True,
            },
        }],
    }


class GitHubConnector(Connector):
    kind = "github"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        options = config.options or {}
        repo = str(options.get("repo") or "")
        if not REPO_RE.match(repo):
            raise ConfigError(
                f"github connector {self.id!r} needs options.repo as 'owner/name'")
        self.repo = repo
        self.api_root = str(options.get("api_root") or API_ROOT).rstrip("/")
        self._lock = threading.RLock()
        self._client: Any = None
        self._schema: Optional[SourceDescriptor] = None
        self._schema_at = 0.0
        self._login = ""
        self._replays: dict[str, dict[str, Any]] = {}

    # ───────────────────────────── transport ──────────────────────────────

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                import httpx
                self._client = httpx.Client(
                    timeout=self.config.timeout, follow_redirects=False,
                    verify=self.config.verify_tls,
                    headers={"Accept": "application/vnd.github+json",
                             "X-GitHub-Api-Version": "2022-11-28",
                             "User-Agent": "morph-connector/1.0"})
            return self._client

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None

    def _headers(self) -> dict[str, str]:
        if not self.config.token_env:
            return {}
        token = get_secret(self.config.token_env)
        if not token:
            # Public repositories read fine without one. Writes are not offered
            # until `describe` has seen an authenticated login.
            return {}
        return {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, path: str, *, params: Any = None,
                 body: Any = None) -> tuple[int, Any, Any]:
        url = f"{self.api_root}{path}"
        headers = self._headers()
        client = self._get_client()
        attempts = self.config.max_retries + 1 if method == "GET" else 2
        for attempt in range(attempts):
            try:
                response = client.request(method, url, params=params, json=body,
                                          headers=headers)
            except Exception as exc:  # noqa: BLE001 - any transport failure is an outage
                if attempt + 1 >= attempts:
                    raise ConnectorError(
                        f"{self.id!r} could not reach GitHub: {type(exc).__name__}") from exc
                time.sleep(self.config.retry_backoff * (2 ** attempt))
                continue
            if self._rate_limited(response) and attempt + 1 < attempts:
                time.sleep(self._wait_for(response, attempt))
                continue
            if 500 <= response.status_code < 600 and attempt + 1 < attempts:
                time.sleep(self.config.retry_backoff * (2 ** attempt))
                continue
            raw = response.content or b""
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ConnectorError(f"{self.id!r}: GitHub returned more than "
                                     f"{MAX_RESPONSE_BYTES} bytes")
            payload = None
            if raw:
                try:
                    payload = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ConnectorError(f"{self.id!r}: GitHub returned a non-JSON body") from exc
            return response.status_code, payload, response
        raise ConnectorError(f"{self.id!r} could not reach GitHub")

    @staticmethod
    def _rate_limited(response: Any) -> bool:
        if response.status_code == 429:
            return True
        # GitHub signals its primary rate limit with 403 and a zero remaining count.
        try:
            return (response.status_code == 403
                    and response.headers.get("x-ratelimit-remaining") == "0")
        except Exception:  # noqa: BLE001
            return False

    def _wait_for(self, response: Any, attempt: int) -> float:
        for header in ("retry-after", "x-ratelimit-reset"):
            try:
                raw = response.headers.get(header)
            except Exception:  # noqa: BLE001
                raw = None
            if not raw:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if header == "x-ratelimit-reset":
                value = max(0.0, value - time.time())
            return max(0.0, min(value, 20.0))
        return self.config.retry_backoff * (2 ** attempt)

    @staticmethod
    def _detail(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict) and payload.get("message"):
            return clean_text(payload["message"], 200)
        return fallback

    # ───────────────────────────── discovery ──────────────────────────────

    def describe(self, *, refresh: bool = False) -> SourceDescriptor:
        with self._lock:
            fresh = self._schema is not None and (time.time() - self._schema_at) < SCHEMA_TTL_SECONDS
            if fresh and not refresh:
                return self._schema  # type: ignore[return-value]
        status, payload, _ = self._request("GET", f"/repos/{self.repo}")
        if status in (401, 403):
            raise ConnectorError(f"{self.id!r}: GitHub refused the credential "
                                 f"({self._detail(payload, 'HTTP ' + str(status))})")
        if status == 404:
            raise ConnectorError(f"{self.id!r}: {self.repo} is not visible to this credential")
        if status != 200:
            raise ConnectorError(f"{self.id!r}: GitHub returned HTTP {status} for {self.repo}")

        login = ""
        if self._headers():
            who_status, who, _ = self._request("GET", "/user")
            if who_status == 200 and isinstance(who, dict):
                login = clean_text(who.get("login"), 60)

        shape = _descriptor(self.repo, login)
        if not login or self.config.read_only:
            # Nothing can be written, so nothing is offered: an operation the
            # agent could propose and nobody could apply is noise in an approval
            # queue.
            shape["entities"][0]["operations"] = []
        described = SourceDescriptor.parse(shape,
                                           connector_id=self.id, kind=self.kind,
                                           fetched_at=time.time())
        with self._lock:
            self._schema, self._schema_at, self._login = described, time.time(), login
        return described

    def health(self) -> dict[str, Any]:
        started = time.time()
        try:
            described = self.describe()
        except Exception as exc:  # noqa: BLE001 - health must never raise
            message = getattr(exc, "message", f"{type(exc).__name__}")
            return {"id": self.id, "kind": self.kind, "ok": False,
                    "error": clean_text(message, 200),
                    "latency_ms": round((time.time() - started) * 1000, 1)}
        return {"id": self.id, "kind": self.kind, "ok": True, "label": described.label,
                "entities": [e.name for e in described.entities],
                "repo": self.repo, "acting_as": self._login or "anonymous (read-only)",
                "writable": bool(self._login) and not self.config.read_only,
                "hint": "" if self._login else (
                    f"set {self.config.token_env or 'a token_env'} in the Hermes profile "
                    f"to let changes be proposed"),
                "latency_ms": round((time.time() - started) * 1000, 1)}

    # ─────────────────────────────── reads ────────────────────────────────

    def fetch(self, entity: str, *, filters: Sequence[Predicate] = (), limit: int = 100,
              sort: Optional[tuple[str, str]] = None) -> FetchResult:
        described = self.entity_or_raise(entity)
        limit = max(1, min(int(limit), described.max_page_size, self.config.max_records))
        params: list[tuple[str, str]] = [("per_page", str(limit)), ("state", "all")]
        # One filter GitHub can answer itself; the rest are applied locally, so the
        # result is identical either way and a source that ignores a hint cannot
        # widen what the operator asked to see.
        for predicate in filters:
            if predicate.field == "state" and predicate.op == "eq":
                params = [p for p in params if p[0] != "state"]
                params.append(("state", str(predicate.value)))
        status, payload, _ = self._request("GET", f"/repos/{self.repo}/issues", params=params)
        if status != 200 or not isinstance(payload, list):
            raise ConnectorError(f"{self.id!r}: GitHub returned HTTP {status} listing issues: "
                                 f"{self._detail(payload, 'no detail')}")
        records = [_issue_record(raw) for raw in payload if isinstance(raw, dict)]
        for predicate in filters:
            records = [r for r in records if _local_match(r, predicate)]
        if sort:
            field, direction = sort
            records.sort(key=lambda r: (r.get(field) is None, str(r.get(field))),
                         reverse=direction == "desc")
        return FetchResult(records=records[:limit], total=len(records),
                           truncated=len(payload) >= limit)

    def get(self, entity: str, record_id: str) -> Optional[dict[str, Any]]:
        self.entity_or_raise(entity)
        record_id = _issue_number(record_id)
        status, payload, _ = self._request("GET", f"/repos/{self.repo}/issues/{record_id}")
        if status == 404:
            return None
        if status != 200 or not isinstance(payload, dict):
            raise ConnectorError(f"{self.id!r}: GitHub returned HTTP {status} for issue "
                                 f"{record_id}")
        return _issue_record(payload)

    def changes(self, since: int = 0) -> dict[str, Any]:
        """GitHub's own incremental read: issues updated after a timestamp.

        This is what lets MORPH notice an issue somebody closed in the GitHub UI.
        """
        params: list[tuple[str, str]] = [("per_page", "100"), ("state", "all"),
                                         ("sort", "updated"), ("direction", "asc")]
        if since:
            params.append(("since", datetime.fromtimestamp(since, tz=timezone.utc)
                           .isoformat().replace("+00:00", "Z")))
        try:
            status, payload, _ = self._request("GET", f"/repos/{self.repo}/issues", params=params)
        except ConnectorError:
            return {"supported": False, "cursor": since, "changes": [],
                    "error": "the GitHub issues feed could not be read"}
        if status != 200 or not isinstance(payload, list):
            return {"supported": False, "cursor": since, "changes": [],
                    "error": f"GitHub returned HTTP {status}"}
        cursor = since
        entries = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            stamp = _epoch(raw.get("updated_at"))
            if since and stamp <= since:
                continue
            cursor = max(cursor, stamp)
            actor = (raw.get("assignee") or raw.get("user") or {}).get("login")
            entries.append({"seq": stamp, "entity": "issues",
                            "record_id": str(raw.get("number") or ""),
                            "operation": "updated", "actor": clean_text(actor, 60),
                            "at": stamp})
        return {"supported": True, "cursor": cursor or int(time.time()),
                "has_more": len(entries) >= 100, "changes": entries}

    # ─────────────────────────────── writes ───────────────────────────────

    def apply(self, entity: str, record_id: str, operation: str, params: dict[str, Any], *,
              expected_version: Optional[int], idempotency_key: str, actor: str) -> ApplyResult:
        if self.config.read_only:
            raise PermissionDenied(
                f"connector {self.id!r} is configured read-only; no write reaches GitHub")
        described = self.entity_or_raise(entity)
        descriptor = described.operation(operation)
        if descriptor is None:
            raise ConnectorError(f"{self.id}.{entity} does not expose operation {operation!r}")

        with self._lock:
            replayed = self._replays.get(idempotency_key) if idempotency_key else None
        if replayed is not None:
            # GitHub accepts no idempotency key, so MORPH's key is honoured here. Every
            # operation offered is set-to-value, so even a lost cache cannot double-apply.
            return ApplyResult(record=dict(replayed["record"]),
                               changed=dict(replayed["changed"]), replayed=True)

        current = self.get(entity, record_id)
        if current is None:
            raise NotFound(f"issue {record_id} does not exist in {self.repo}")
        if expected_version is not None and int(current.get("_version") or 0) != expected_version:
            raise StaleRecord(
                f"issue {record_id} was updated on GitHub after the proposal was built "
                f"(updated_at moved); review it and propose again")
        for predicate in descriptor.precondition:
            check = Predicate(str(predicate.get("field")), str(predicate.get("op") or "eq"),
                              predicate.get("value"))
            if not _local_match(current, check):
                raise StaleRecord(str(predicate.get("message") or "precondition not met"))

        body = self._body_for(operation, params)
        status, payload, _ = self._request(
            "PATCH", f"/repos/{self.repo}/issues/{_issue_number(record_id)}", body=body)
        if status == 404:
            raise NotFound(f"issue {record_id} is no longer reachable in {self.repo}")
        if status in (401, 403):
            raise ConnectorError(f"{self.id!r}: GitHub refused the write "
                                 f"({self._detail(payload, 'HTTP ' + str(status))})")
        if status in (410, 422):
            raise ConnectorError(f"{self.id!r}: GitHub refused the operation "
                                 f"({self._detail(payload, 'unprocessable')})")
        if status != 200 or not isinstance(payload, dict):
            raise ConnectorError(f"{self.id!r}: GitHub returned HTTP {status} applying "
                                 f"{operation!r}")

        record = _issue_record(payload)
        changed = {key: record.get(key) for key in descriptor.predict(params)}
        if idempotency_key:
            with self._lock:
                self._replays[idempotency_key] = {"record": dict(record),
                                                  "changed": dict(changed)}
        return ApplyResult(record=record, changed=changed, replayed=False)

    def normalise_params(self, entity: str, operation: str,
                         params: dict[str, Any]) -> dict[str, Any]:
        if operation == "set_labels":
            return {**params, "labels": _join_labels(_split_labels(params.get("labels")))}
        if operation == "set_title":
            return {**params, "title": clean_text(params.get("title"), 160)}
        return params

    def _body_for(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if operation == "close_issue":
            return {"state": "closed", "state_reason": "completed"}
        if operation == "close_as_not_planned":
            return {"state": "closed", "state_reason": "not_planned"}
        if operation == "set_labels":
            return {"labels": _split_labels(params.get("labels"))}
        if operation == "set_title":
            title = clean_text(params.get("title"), 160)
            if not title:
                raise ConnectorError("an issue cannot have an empty title")
            return {"title": title}
        if operation == "unassign":
            return {"assignees": []}
        if operation == "reopen_issue":
            return {"state": "open"}
        if operation == "assign_to_me":
            if not self._login:
                raise ConnectorError(f"{self.id!r} is not authenticated, so it cannot assign")
            return {"assignees": [self._login]}
        raise ConnectorError(f"{self.id!r} has no request shape for {operation!r}")
