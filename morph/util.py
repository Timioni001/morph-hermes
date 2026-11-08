"""Small shared helpers: text hygiene, digests, time, risk ordering."""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from typing import Any, Iterable

RISK_ORDER = ("low", "medium", "high")
# Built from escapes on purpose: writing these characters literally would put
# invisible and bidi-control codepoints in MORPH's own source, which is exactly
# what this pattern exists to strip out of text crossing a trust boundary.
# Two different problems, two different fixes. Control characters become a space
# so words do not run together; zero-width and bidi-control characters are deleted,
# because they sit *inside* words and exist only to make text render differently
# from how it is stored.
_CONTROL = re.compile("[\u0000-\u0008\u000b-\u001f\u007f-\u009f]")
_INVISIBLE = re.compile("[\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")
IDENT = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_REF = re.compile(r"^[a-z][a-z0-9_]{0,39}(\.[a-z][a-z0-9_]{0,39}){1,2}$")


def now() -> float:
    return time.time()


def clean_text(value: Any, limit: int = 160) -> str:
    """Flatten any value to a short, single-line, control-character-free string.

    Used on everything that crosses a trust boundary — labels from a connected
    application, strings a model produced, operator notes — before it is stored,
    rendered or put in a prompt.  Bidi and zero-width characters go too: they let
    text render differently from how it is stored.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if not isinstance(value, str):
        value = str(value)
    value = unicodedata.normalize("NFC", value)
    value = _INVISIBLE.sub("", _CONTROL.sub(" ", value)).replace("\t", " ")
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        value = value[: max(0, limit - 1)].rstrip() + "…"
    return value


def is_ref(value: Any, parts: int = 2) -> bool:
    """Whether *value* is a dotted reference such as ``storefront.orders`` (2) or
    ``storefront.orders.flag_for_review`` (3)."""
    return isinstance(value, str) and bool(_REF.match(value)) and value.count(".") == parts - 1


def digest(value: Any) -> str:
    """Stable short digest of any JSON-serialisable value."""
    blob = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def chain_hash(previous: str, payload: Any) -> str:
    """Next link of the audit hash chain."""
    body = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(f"{previous}\n{body}".encode("utf-8")).hexdigest()


def risk_at_most(risk: str, ceiling: str) -> bool:
    try:
        return RISK_ORDER.index(risk) <= RISK_ORDER.index(ceiling)
    except ValueError:
        return False


def match_pattern(pattern: str, target: str) -> bool:
    """Glob-lite matching over dotted references: ``*`` matches one segment,
    ``**`` matches the rest, and a bare ``*`` matches anything."""
    if pattern == "*" or pattern == "**":
        return True
    pat, got = pattern.split("."), target.split(".")
    for i, segment in enumerate(pat):
        if segment == "**":
            return True
        if i >= len(got):
            return False
        if segment != "*" and segment != got[i]:
            return False
    return len(pat) == len(got)


def first(items: Iterable[Any], default: Any = None) -> Any:
    for item in items:
        return item
    return default
