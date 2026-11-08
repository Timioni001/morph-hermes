"""MORPH dashboard API routes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

_core_path = (
    Path(__file__).resolve().parents[1] / "core.py"
)

_spec = importlib.util.spec_from_file_location(
    "morph_shared_core",
    _core_path
)

if _spec is None or _spec.loader is None:
    raise RuntimeError(
        "Could not load MORPH engine"
    )

core = sys.modules.get(
    "morph_shared_core"
)

if core is None:
    core = importlib.util.module_from_spec(_spec)
    sys.modules["morph_shared_core"] = core
    _spec.loader.exec_module(core)

router = APIRouter()


class Transform(BaseModel):
    goal: str = Field(
        min_length=2,
        max_length=400
    )


class Proposal(BaseModel):
    source: str
    record_id: str
    operation: str


@router.get("/state")
def state():
    return core.snapshot()


@router.get("/catalog")
def catalog():
    return core.catalog()


@router.post("/quick-transform")
def quick_transform(body: Transform):
    # Local preset generation.
    # AI composition is provided by morph_design.
    try:
        return core.compose(
            body.goal,
            mode="preset"
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        ) from exc


@router.post("/propose")
def propose(body: Proposal):
    try:
        return core.propose_action(
            body.source,
            body.record_id,
            body.operation
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        ) from exc


@router.post("/approve/{action_id}")
def approve(action_id: str):
    try:
        return core.approve_action(
            action_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc)
        ) from exc


@router.post("/reject/{action_id}")
def reject(action_id: str):
    try:
        return core.reject_action(
            action_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc)
        ) from exc
