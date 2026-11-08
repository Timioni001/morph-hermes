"""MORPH's dashboard backend, mounted by Hermes at ``/api/plugins/morph/``.

Hermes authenticates these routes before they run (and refuses them outright when
the plugin is disabled), so this module's job is narrower but not small:

* decide **who** is calling and with what authority — the verified dashboard
  session's identity maps to a configured role; a loopback dashboard with no auth
  gate engaged is the local operator;
* keep approval on this side of the boundary: operator authority exists only here
  and in the interactive CLI, never in a tool handler;
* refuse a cross-site caller on every mutating route, so a page the operator
  happens to have open cannot drive their dashboard;
* stay off the event loop — the engine does blocking SQLite and HTTP work, and the
  plugin host warns that a blocking handler stalls everything else.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from morph import cli as morph_cli, engine, store  # noqa: E402
from morph.actions import Principal  # noqa: E402
from morph.errors import MorphError  # noqa: E402

router = APIRouter()

# A custom header a cross-origin form or image cannot set without a CORS preflight;
# paired with the browser's own Sec-Fetch-Site verdict when it sends one.
INTENT_HEADER = "X-MORPH-Request"


def principal_for(request: Request) -> Principal:
    """Who is calling, as MORPH understands it.

    Hermes puts a verified :class:`Session` on ``request.state.session` when its auth
    gate is engaged.  Its identity selects a role from ``user_roles``; without a
    session (a loopback dashboard, which Hermes serves unauthenticated by design)
    the caller is the local operator on the default role.  Either way the authority
    is ``OPERATOR``: this code path is only reachable through the dashboard Hermes
    itself has already admitted.
    """
    session = getattr(request.state, "session", None)
    identity = ""
    display = ""
    if session is not None:
        identity = (getattr(session, "email", "") or getattr(session, "user_id", "")
                    or getattr(session, "principal", ""))
        display = getattr(session, "display_name", "") or identity
    return Principal.operator(identity, display or "Local operator")


def _guard_mutation(request: Request, intent: str) -> None:
    """Refuse a request a browser says came from another site, or that did not set
    MORPH's intent header."""
    fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site and fetch_site not in ("same-origin", "same-site", "none"):
        raise HTTPException(403, "cross-site requests are refused")
    if (intent or "").strip() != "1":
        raise HTTPException(403, f"{INTENT_HEADER}: 1 is required on requests that change state")


async def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run blocking engine work on a worker thread, translating MORPH errors."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except MorphError as exc:
        raise HTTPException(exc.status, detail=exc.payload()) from exc


# ─────────────────────────────── request bodies ────────────────────────────────

class GoalBody(BaseModel):
    goal: str = Field(min_length=3, max_length=400)
    preserve_context: bool = True


class ContextBody(BaseModel):
    selection: dict[str, str] = Field(default_factory=dict)
    pinned_filters: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=200)


class ProposeBody(BaseModel):
    source: str = Field(min_length=1, max_length=48)
    entity: str = Field(min_length=1, max_length=48)
    record_id: str = Field(min_length=1, max_length=80)
    operation: str = Field(min_length=1, max_length=48)
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=200)
    idempotency_key: str = Field(default="", max_length=100)


class ApproveBody(BaseModel):
    confirm_token: str = Field(min_length=8, max_length=120)


class ApproveManyBody(BaseModel):
    items: list[dict[str, str]] = Field(default_factory=list)


class RejectBody(BaseModel):
    reason: str = Field(default="", max_length=200)


# ───────────────────────────────── read routes ─────────────────────────────────

@router.get("/state")
async def state(request: Request, refresh: bool = False) -> Any:
    return await _run(engine.state, principal_for(request), refresh=refresh)


@router.get("/discover")
async def discover(request: Request, refresh: bool = False) -> Any:
    return await _run(engine.discover, principal_for(request), refresh=refresh)


@router.get("/proposals")
async def proposals(request: Request, include_decided: bool = False) -> Any:
    principal = principal_for(request)
    if include_decided:
        return await _run(engine.proposals, principal,
                          statuses=("pending", "applying", "applied", "unverified", "failed",
                                    "rejected", "stale", "expired"), limit=60)
    return await _run(engine.proposals, principal)


@router.get("/record/{source}/{entity}/{record_id}")
async def record(request: Request, source: str, entity: str, record_id: str) -> Any:
    return await _run(engine.record_detail, principal_for(request), source, entity, record_id)


@router.get("/verify/{proposal_id}")
async def verify(proposal_id: str) -> Any:
    return await _run(engine.verify, proposal_id)


@router.get("/audit")
async def audit(limit: int = 40) -> Any:
    def read() -> dict[str, Any]:
        return {"chain": store.verify_audit_chain(), "entries": store.audit_tail(limit)}
    return await _run(read)


@router.get("/doctor")
async def doctor() -> Any:
    return await _run(morph_cli.doctor)


# ──────────────────────────────── write routes ─────────────────────────────────

@router.post("/goal")
async def set_goal(request: Request, body: GoalBody,
                   intent: str = Header(default="", alias=INTENT_HEADER)) -> Any:
    _guard_mutation(request, intent)
    from morph import hostllm
    return await _run(engine.plan_workspace, principal_for(request), body.goal,
                      llm=hostllm.current(), preserve_context=body.preserve_context)


@router.post("/refresh")
async def refresh(request: Request,
                  intent: str = Header(default="", alias=INTENT_HEADER)) -> Any:
    _guard_mutation(request, intent)
    return await _run(engine.refresh_data, principal_for(request))


@router.post("/context")
async def set_context(request: Request, body: ContextBody,
                      intent: str = Header(default="", alias=INTENT_HEADER)) -> Any:
    _guard_mutation(request, intent)
    patch = {"selection": body.selection, "pinned_filters": body.pinned_filters,
             "clear": body.clear}
    if body.note:
        patch["note"] = body.note
    return await _run(engine.set_context, principal_for(request), patch)


@router.post("/propose")
async def propose(request: Request, body: ProposeBody,
                  intent: str = Header(default="", alias=INTENT_HEADER)) -> Any:
    _guard_mutation(request, intent)
    return await _run(engine.propose, principal_for(request), source=body.source,
                      entity=body.entity, record_id=body.record_id, operation=body.operation,
                      params=body.params, reason=body.reason,
                      idempotency_key=body.idempotency_key)


@router.post("/approve/{proposal_id}")
async def approve(request: Request, proposal_id: str, body: ApproveBody,
                  intent: str = Header(default="", alias=INTENT_HEADER)) -> Any:
    """Apply a pending action. The only authority boundary that matters in MORPH:
    reaching this route means an authenticated human is driving the dashboard."""
    _guard_mutation(request, intent)
    return await _run(engine.approve, principal_for(request), proposal_id,
                      confirm_token=body.confirm_token)


@router.post("/approve-batch")
async def approve_batch(request: Request, body: ApproveManyBody,
                        intent: str = Header(default="", alias=INTENT_HEADER)) -> Any:
    _guard_mutation(request, intent)
    return await _run(engine.approve_many, principal_for(request), body.items)


@router.post("/reject/{proposal_id}")
async def reject(request: Request, proposal_id: str, body: RejectBody,
                 intent: str = Header(default="", alias=INTENT_HEADER)) -> Any:
    _guard_mutation(request, intent)
    return await _run(engine.reject, principal_for(request), proposal_id, body.reason)


@router.post("/revisions/{revision}/restore")
async def restore(request: Request, revision: int,
                  intent: str = Header(default="", alias=INTENT_HEADER)) -> Any:
    _guard_mutation(request, intent)
    return await _run(engine.restore_revision, principal_for(request), revision)


@router.post("/reload")
async def reload(request: Request,
                 intent: str = Header(default="", alias=INTENT_HEADER)) -> Any:
    _guard_mutation(request, intent)
    return await _run(engine.reload)
