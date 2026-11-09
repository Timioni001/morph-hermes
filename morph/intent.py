"""Intent interpretation: what the operator actually asked for.

The planner used to score the capability graph against the raw words of a goal and
go straight to composing. That worked, but it could not answer two questions the
product needs to answer out loud:

* **What kind of work is this?** "show me", "compare", and "flag the worst one" want
  different interfaces. A review goal wants a queue; an analysis goal wants more
  chart and less form; an action goal wants the action path first.
* **What did we not find?** A goal that mentions refunds when no connected
  application exposes a refund operation should say so, not quietly compose a
  workspace about something adjacent. Reporting a missing prerequisite is more
  useful than silently answering a different question.

So interpretation is its own stage with its own output: an :class:`Intent`. Both
planners consume it — the deterministic composer directly, the model as a labelled
hint it is free to disagree with (the capability graph, not the hint, is what the
validator enforces).

Nothing here is a model call. It is deterministic, inspectable and fast, which is
what makes it a sound floor for a stage the model also participates in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable, Optional

from morph.capability import CapabilityGraph
from morph.connectors import EntityDescriptor
from morph.util import clean_text

STOPWORDS = frozenset("""
a an and are as at be been being but by can could do does for from give had has have her his how i
if in into is it its me my need needs of on or our out over so some that the their them then there
these they this those to up us want wants was we were what when where which who why will with would
you your about across all also any because build make made see view workspace screen page dashboard
interface please track tracking manage managing handle let lets get got just now today really very
something anything everyone someone thing things work working show list find give want need through
worst best most least more less other another same each every both few many much such own here
right left back next last first then than while when during into onto upon still yet ever never
please kindly maybe perhaps quickly simply exactly actually basically currently
""".split())

# Verb families. The words are the operator's, so they are matched loosely; the
# consequence is a weighting, never a restriction on what the workspace may contain.
VERB_FAMILIES: dict[str, tuple[str, ...]] = {
    # Deliberately no "deal", "do", "sort", "action" or "process": every one of them
    # is as likely to be a noun naming an entity as a verb naming an intention, and a
    # goal about deals is not a goal to deal with something.
    "act": ("fix", "flag", "resolve", "escalate", "request", "approve", "assign", "close",
            "cancel", "refund", "restock", "reorder", "expedite", "contact", "follow",
            "chase", "handle", "clear", "reply", "respond", "update", "mark"),
    "review": ("review", "triage", "check", "inspect", "audit", "queue", "backlog",
               "outstanding", "pending", "unresolved", "open", "overdue", "late",
               "stuck", "blocked", "failing", "problem", "issue", "complaint", "risk"),
    "analyse": ("compare", "trend", "breakdown", "distribution", "total", "sum", "average",
                "count", "many", "much", "rate", "ratio", "performance", "report",
                "analytics", "analyse", "analyze", "measure", "metric", "summary"),
    "monitor": ("monitor", "watch", "live", "status", "health", "now", "current",
                "realtime", "ongoing", "today", "alert"),
}

# Words that name a kind of thing MORPH might be asked about but may not have. Used
# only to phrase a missing prerequisite in the operator's own vocabulary.
_WORD = re.compile(r"[a-z0-9]+")
MAX_FOCUS = 4

# Operations vocabulary rarely matches a schema's vocabulary. People say "complaint"
# where the Helpdesk says "case", and "stock" where the Warehouse says "inventory".
# This is a small, declared, inspectable expansion applied to the GOAL only — never
# to anything an application published, so it can never invent a field or an
# operation. Add to it when a real goal misses; do not let it become a thesaurus.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "complaint": ("case", "ticket", "issue", "support"),
    "ticket": ("case", "issue"),
    "issue": ("case", "ticket"),
    "client": ("customer", "account"),
    "buyer": ("customer",),
    "stock": ("inventory", "item"),
    "inventory": ("stock", "item"),
    "late": ("delayed", "overdue", "slow"),
    "overdue": ("delayed", "late"),
    "unhappy": ("negative", "complaint"),
    "angry": ("negative", "urgent"),
    "refund": ("refunded", "payment"),
    "payment": ("paid", "billing"),
    "delivery": ("shipment", "shipping", "carrier"),
    "shipping": ("shipment", "carrier"),
    "parcel": ("shipment",),
    "agent": ("assignee", "owner"),
    "rep": ("agent", "assignee"),
    "restock": ("reorder", "replenish"),
    "vip": ("priority", "tier"),
}


def tokens_of(text: str) -> list[str]:
    """The operator's own words, plus crude stems so "delays" matches "delayed"."""
    words = [w for w in _WORD.findall((text or "").lower()) if len(w) > 2 and w not in STOPWORDS]
    out: list[str] = []
    for word in words:
        out.append(word)
        for suffix in ("ing", "ed", "es", "s"):
            if len(word) > len(suffix) + 3 and word.endswith(suffix):
                out.append(word[: -len(suffix)])
                break
    seen: set[str] = set()
    return [w for w in out if not (w in seen or seen.add(w))]


def expansions_of(tokens: Iterable[str]) -> list[str]:
    """Declared synonyms for the operator's words, minus any they already said.

    These are scored at a fraction of a direct hit (:data:`WEAK_WEIGHT`). "Delayed
    orders and the complaints about them" must still be primarily about orders:
    the expansion of "complaints" should pull support cases into focus, not take
    the goal over.
    """
    direct = set(tokens)
    out: list[str] = []
    for token in tokens:
        for synonym in SYNONYMS.get(token, ()):
            if synonym not in direct and synonym not in out:
                out.append(synonym)
    return out


WEAK_WEIGHT = 0.35


def _hits(tokens: Iterable[str], text: str) -> int:
    haystack = (text or "").lower().replace("_", " ")
    return sum(1 for token in tokens if token in haystack)


def _weighted(tokens: Iterable[str], weak: Iterable[str], text: str) -> float:
    """Direct word matches at full weight, declared synonyms at a fraction."""
    return _hits(tokens, text) + WEAK_WEIGHT * _hits(weak, text)


# ───────────────────────────────── relevance ───────────────────────────────────

@dataclass
class Relevance:
    """How well each part of the capability graph answers the goal."""

    entity_scores: dict[str, float] = field(default_factory=dict)
    field_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    enum_hits: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    operation_scores: dict[str, float] = field(default_factory=dict)
    matched_tokens: set[str] = field(default_factory=set)

    def ranked_entities(self) -> list[str]:
        return sorted(self.entity_scores, key=lambda ref: (-self.entity_scores[ref], ref))

    def ranked_fields(self, ref: str) -> list[str]:
        scores = self.field_scores.get(ref, {})
        return sorted(scores, key=lambda name: (-scores[name], name))


def score(goal: str, graph: CapabilityGraph) -> Relevance:
    """Rank every entity, field, enum value and operation against the goal's words."""
    tokens = tokens_of(goal)
    weak = expansions_of(tokens)
    out = Relevance()
    for ref, entity in graph.entities.items():
        app = ref.split(".", 1)[0]
        total = 0.0
        for text, weight in ((entity.name, 6.0), (entity.label, 5.0), (app, 2.0),
                             (entity.description, 1.0)):
            matches = _weighted(tokens, weak, text)
            if matches:
                total += weight * matches
                out.matched_tokens.update(t for t in tokens if t in (text or "").lower())
        field_scores: dict[str, float] = {}
        enum_hits: list[tuple[str, str]] = []
        for descriptor in entity.fields:
            fscore = (2.5 * _weighted(tokens, weak, descriptor.name)
                      + 2.0 * _weighted(tokens, weak, descriptor.label))
            fscore += 0.5 * _weighted(tokens, weak, descriptor.description)
            if fscore:
                out.matched_tokens.update(
                    t for t in tokens
                    if t in f"{descriptor.name} {descriptor.label} {descriptor.description}".lower())
            for value in descriptor.values:
                if _hits(tokens, value):
                    fscore += 3.0
                    enum_hits.append((descriptor.name, value))
                    out.matched_tokens.update(t for t in tokens if t in value.lower())
                elif _hits(weak, value):
                    # A synonym can point at a value but never silently filter on it.
                    fscore += 3.0 * WEAK_WEIGHT
            # Tie-breaks that keep workspaces readable rather than arbitrary.
            if descriptor.role in ("status", "metric", "amount", "title", "flag"):
                fscore += 0.4
            if descriptor.name in (entity.primary_key, entity.title_field):
                fscore += 0.6
            field_scores[descriptor.name] = fscore
            total += min(fscore, 6.0)
        for operation in entity.operations:
            op_score = (4.0 * _weighted(tokens, weak, operation.id)
                        + 3.0 * _weighted(tokens, weak, operation.label))
            op_score += 1.0 * _weighted(tokens, weak, operation.description)
            if op_score:
                out.matched_tokens.update(
                    t for t in tokens if t in f"{operation.id} {operation.label}".lower())
            out.operation_scores[f"{ref}.{operation.id}"] = op_score
            total += min(op_score, 5.0)
        total += 0.3 * len(graph.links_touching(ref))
        out.entity_scores[ref] = total
        out.field_scores[ref] = field_scores
        out.enum_hits[ref] = enum_hits
    return out


# ────────────────────────────────── intent ─────────────────────────────────────

@dataclass(frozen=True)
class Focus:
    """One entity the goal is about, and why."""

    ref: str
    score: float
    named: bool                               # the goal used this entity's own words
    filters: tuple[dict[str, Any], ...] = ()  # filters the goal's words imply
    reason: str = ""

    def public(self) -> dict[str, Any]:
        return {"entity": self.ref, "score": round(self.score, 1), "named": self.named,
                "filters": [dict(f) for f in self.filters], "why": self.reason}


@dataclass(frozen=True)
class Intent:
    """A structured reading of the goal, against this capability graph."""

    goal: str
    mode: str                                  # act | review | analyse | monitor | explore
    secondary_modes: tuple[str, ...] = ()
    focus: tuple[Focus, ...] = ()
    metrics: tuple[str, ...] = ()              # "<entity>.<field>" worth aggregating
    operations: tuple[str, ...] = ()           # operation refs the goal implies
    time_fields: tuple[str, ...] = ()          # "<entity>.<field>" datetime axes available
    board_fields: tuple[str, ...] = ()         # "<entity>.<field>" enums worth a board
    unmatched_terms: tuple[str, ...] = ()      # goal words nothing published answers
    missing: tuple[str, ...] = ()              # prerequisites, phrased for a human
    relevance: Optional[Relevance] = None

    @property
    def primary(self) -> str:
        return self.focus[0].ref if self.focus else ""

    @property
    def wants_action_path(self) -> bool:
        return self.mode == "act" or bool(self.operations)

    def public(self) -> dict[str, Any]:
        return {
            "goal": self.goal, "mode": self.mode,
            "also": list(self.secondary_modes),
            "focus": [f.public() for f in self.focus],
            "metrics": list(self.metrics), "operations": list(self.operations),
            "time_fields": list(self.time_fields), "board_fields": list(self.board_fields),
            "unmatched_terms": list(self.unmatched_terms), "missing": list(self.missing),
        }

    def hint(self) -> dict[str, Any]:
        """The compact form handed to the model, labelled as a hint in the prompt."""
        return {"mode": self.mode, "likely_focus": [f.ref for f in self.focus],
                "implied_filters": {f.ref: [dict(x) for x in f.filters]
                                    for f in self.focus if f.filters},
                "candidate_operations": list(self.operations),
                "unmatched_terms": list(self.unmatched_terms)}


def _substantive_unmatched(tokens: list[str], matched: set[str]) -> tuple[str, ...]:
    """Goal words nothing published answers, with the noise taken out.

    Stems are dropped when their longer form is also unmatched ("complaint" after
    "complaints"), and very short words are ignored: reporting them as missing
    prerequisites would cry wolf on ordinary English.
    """
    vocabulary = _verb_vocabulary()
    candidates = [t for t in tokens
                  if t not in matched and len(t) > 4 and t not in vocabulary]
    out: list[str] = []
    for word in candidates:
        if any(other != word and other.startswith(word) for other in candidates):
            continue
        out.append(word)
    return tuple(out)


@lru_cache(maxsize=1)
def _verb_vocabulary() -> frozenset[str]:
    """Every mode word and its stem. A word that signalled *how* to work is not also
    a missing data prerequisite."""
    words: set[str] = set()
    for family in VERB_FAMILIES.values():
        for word in family:
            words.add(word)
            words.update(tokens_of(word))
    return frozenset(words)


def _modes(goal: str) -> tuple[str, tuple[str, ...]]:
    # Token matching, not substring matching: "contact" must not count as "act".
    tokens = set(tokens_of(goal))
    scores = {name: sum(1 for word in words if word in tokens)
              for name, words in VERB_FAMILIES.items()}
    ranked = sorted((name for name, count in scores.items() if count),
                    key=lambda name: (-scores[name], name))
    if not ranked:
        return "explore", ()
    return ranked[0], tuple(ranked[1:3])


def _filters_for(ref: str, entity: EntityDescriptor, relevance: Relevance, tokens: list[str],
                 *, named: bool, action_tokens: frozenset[str] = frozenset()
                 ) -> tuple[dict[str, Any], ...]:
    """Filters the goal's own words imply, e.g. "delayed orders" → status = delayed.

    Two rules keep this from over-reaching:

    * For an entity the goal never named, a matched enum value only becomes a filter
      when the goal also names that field. Without it, "stock running low" would
      narrow an unrelated entity that happens to publish "low" as a priority value.
    * A word that named an *operation* does not also become a filter. "Flag the worst
      delayed order" asks to flag something; filtering to records already flagged
      would show exactly the ones that do not need the action.
    """
    chosen: dict[str, list[str]] = {}
    for name, value in relevance.enum_hits.get(ref, []):
        if action_tokens and any(token in value.lower() for token in action_tokens):
            continue
        if not named:
            descriptor = entity.field(name)
            if descriptor is None or not (_hits(tokens, descriptor.name)
                                          or _hits(tokens, descriptor.label)):
                continue
        chosen.setdefault(name, [])
        if value not in chosen[name]:
            chosen[name].append(value)
    out: list[dict[str, Any]] = []
    for name, values in list(chosen.items())[:2]:
        if len(values) == 1:
            out.append({"field": name, "op": "eq", "value": values[0]})
        else:
            out.append({"field": name, "op": "in", "value": values})
    return tuple(out)


def interpret(goal: str, graph: CapabilityGraph, relevance: Optional[Relevance] = None, *,
              denied_operations: Optional[dict[str, str]] = None) -> Intent:
    """Read a goal against what the connected applications can actually do.

    ``denied_operations`` maps an operation ref to the reason this caller may not run
    it. Passing it lets interpretation tell the difference between "nothing can do
    that" and "something can, but not for you" — two very different answers.
    """
    goal = clean_text(goal, 400)
    relevance = relevance or score(goal, graph)
    tokens = tokens_of(goal)
    mode, secondary = _modes(goal)
    denied_operations = denied_operations or {}

    # Words that picked out an operation; they describe the action, not the records.
    action_tokens = frozenset(
        token for ref, value in relevance.operation_scores.items() if value > 0
        for token in tokens
        if token in ref.rsplit(".", 1)[-1] or token in ref.rsplit(".", 1)[-1].replace("_", ""))

    ranked = relevance.ranked_entities()
    focus: list[Focus] = []
    for ref in ranked[:MAX_FOCUS]:
        entity = graph.entity(ref)
        if entity is None:
            continue
        value = relevance.entity_scores[ref]
        if focus and value <= 0:
            continue
        named = bool(_hits(tokens, entity.name) or _hits(tokens, entity.label))
        filters = _filters_for(ref, entity, relevance, tokens, named=named,
                               action_tokens=action_tokens)
        if filters:
            reason = "narrowed by " + ", ".join(
                f"{f['field']} {f['op']} "
                f"{', '.join(map(str, f['value'])) if isinstance(f['value'], list) else f['value']}"
                for f in filters)
        elif named:
            reason = "named in the goal"
        else:
            reason = "related to what the goal is about"
        focus.append(Focus(ref=ref, score=value, named=named, filters=filters, reason=reason))

    metrics: list[str] = []
    time_fields: list[str] = []
    board_fields: list[str] = []
    for entry in focus:
        entity = graph.entity(entry.ref)
        if entity is None:
            continue
        scores = relevance.field_scores.get(entry.ref, {})
        numeric = sorted((f for f in entity.fields if f.numeric and f.name != entity.primary_key),
                         key=lambda f: -(scores.get(f.name, 0.0) + (0.8 if f.role == "amount" else 0)))
        metrics += [f"{entry.ref}.{f.name}" for f in numeric[:2]]
        time_fields += [f"{entry.ref}.{f.name}" for f in entity.fields
                        if f.type == "datetime"][:2]
        board_fields += [f"{entry.ref}.{f.name}" for f in entity.fields
                         if f.type == "enum" and 2 <= len(f.values) <= 8]

    operations: list[str] = []
    for ref, operation in graph.writable_operations:
        if relevance.operation_scores.get(ref, 0.0) > 0:
            operations.append(ref)
    if not operations and mode == "act" and focus:
        entity = graph.entity(focus[0].ref)
        if entity is not None:
            operations = [f"{focus[0].ref}.{op.id}" for op in entity.operations[:3]]

    # What the operator said that nothing we are connected to answers. This is the
    # honest half of interpretation: it is how MORPH says "I cannot do that part".
    # Report missing prerequisites in the operator's own words: an expansion that
    # found nothing is not a word they typed.
    unmatched = _substantive_unmatched(tokens, relevance.matched_tokens)
    missing: list[str] = []
    if not focus or (focus and focus[0].score <= 0):
        missing.append(
            "nothing the connected applications publish matches this goal; MORPH composed an "
            "overview instead")
    # An operation the goal asked for that exists but this caller may not run is a
    # different answer from one that does not exist, and the operator deserves it.
    blocked = [f"{ref} ({reason})" for ref, reason in denied_operations.items()
               if any(token in ref.rsplit(".", 1)[-1] for token in tokens)]
    if blocked:
        missing.append("asked for, but not permitted for you: " + "; ".join(blocked[:3]))
    if mode == "act" and not operations and not blocked:
        missing.append(
            "the goal asks for something to be done, but no connected entity in scope exposes a "
            "matching operation")
    if len(unmatched) >= 2 and focus:
        missing.append("nothing connected covers: " + ", ".join(unmatched[:4]))

    return Intent(goal=goal, mode=mode, secondary_modes=secondary, focus=tuple(focus),
                  metrics=tuple(dict.fromkeys(metrics)), operations=tuple(dict.fromkeys(operations)),
                  time_fields=tuple(dict.fromkeys(time_fields)),
                  board_fields=tuple(dict.fromkeys(board_fields)),
                  unmatched_terms=unmatched, missing=tuple(missing), relevance=relevance)
