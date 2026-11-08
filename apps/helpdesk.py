"""Helpdesk — an independent customer-support application.

Separate process, separate database, separate token.  It knows nothing about the
Storefront or the Warehouse; the only thing tying its records to theirs is the
``order_ref`` field, which MORPH links up through a configured cross-application
relationship.
"""
from __future__ import annotations

from apps._runtime import AppSpec, EntitySpec, FieldSpec, OperationSpec, build_app

_PRIORITY = ("low", "normal", "high", "urgent")
_STATUS = ("new", "open", "pending", "escalated", "resolved")
_CHANNEL = ("email", "chat", "phone")
_SENTIMENT = ("positive", "neutral", "negative")

CASES = EntitySpec(
    name="cases",
    label="Support cases",
    description="Customer conversations, their priority and resolution state.",
    title_field="subject",
    fields=(
        FieldSpec("id", "id", "Case", role="key"),
        FieldSpec("subject", "string", "Subject", role="title"),
        FieldSpec("customer_email", "email", "Customer"),
        FieldSpec("order_ref", "string", "Order reference"),
        FieldSpec("priority", "enum", "Priority", values=_PRIORITY, role="status", writable=True),
        FieldSpec("status", "enum", "Status", values=_STATUS, role="status", writable=True),
        FieldSpec("channel", "enum", "Channel", values=_CHANNEL),
        FieldSpec("opened_at", "datetime", "Opened", role="timestamp"),
        FieldSpec("last_reply_at", "datetime", "Last reply", role="timestamp", writable=True),
        FieldSpec("age_hours", "int", "Age", role="metric", unit="h"),
        FieldSpec("assignee", "string", "Assignee", writable=True),
        FieldSpec("sentiment", "enum", "Sentiment", values=_SENTIMENT, role="status"),
        FieldSpec("reply_count", "int", "Replies", role="metric"),
        FieldSpec("escalation_reason", "text", "Escalation reason", writable=True),
        FieldSpec("resolution_note", "text", "Resolution", writable=True),
    ),
    relationships=(
        {"name": "owner", "target_entity": "agents", "local_field": "assignee",
         "target_field": "id", "cardinality": "one"},
    ),
    operations=(
        OperationSpec(
            id="escalate_case", label="Escalate case",
            description="Raise the case priority and route it to the escalation queue.",
            effect={"status": "escalated", "priority": "{priority}", "escalation_reason": "{reason}"},
            params=(
                FieldSpec("reason", "text", "Reason", required=True, max_length=240),
                FieldSpec("priority", "enum", "New priority", values=("high", "urgent"),
                          required=True, description="Priority to escalate to."),
            ),
            precondition=({"field": "status", "op": "nin", "value": ["escalated", "resolved"],
                           "message": "case is already escalated or resolved"},),
            risk="medium",
        ),
        OperationSpec(
            id="resolve_case", label="Resolve case",
            description="Close the case with a resolution note.",
            effect={"status": "resolved", "resolution_note": "{resolution}"},
            params=(FieldSpec("resolution", "text", "Resolution", required=True, max_length=240),),
            precondition=({"field": "status", "op": "neq", "value": "resolved",
                           "message": "case is already resolved"},),
            risk="medium",
        ),
        OperationSpec(
            id="assign_case", label="Assign case",
            description="Assign the case to a support agent.",
            effect={"assignee": "{assignee}", "status": "open"},
            params=(FieldSpec("assignee", "string", "Agent id", required=True, max_length=40),),
            risk="low",
        ),
    ),
    seed=(
        {"id": "CASE-5101", "subject": "Delivery has not arrived", "customer_email": "amara@example.com",
         "order_ref": "ORD-2041", "priority": "high", "status": "open", "channel": "email",
         "opened_at": "2026-11-04T08:30:00Z", "last_reply_at": "2026-11-06T10:10:00Z", "age_hours": 96,
         "assignee": "AG-1", "sentiment": "negative", "reply_count": 4,
         "escalation_reason": None, "resolution_note": None},
        {"id": "CASE-5102", "subject": "Requesting a delivery ETA", "customer_email": "ada@example.com",
         "order_ref": "ORD-2043", "priority": "normal", "status": "open", "channel": "chat",
         "opened_at": "2026-11-05T12:00:00Z", "last_reply_at": "2026-11-06T09:00:00Z", "age_hours": 70,
         "assignee": "AG-2", "sentiment": "neutral", "reply_count": 2,
         "escalation_reason": None, "resolution_note": None},
        {"id": "CASE-5103", "subject": "Order missing tracking information", "customer_email": "ife@example.com",
         "order_ref": "ORD-2045", "priority": "high", "status": "new", "channel": "email",
         "opened_at": "2026-11-06T15:45:00Z", "last_reply_at": None, "age_hours": 40,
         "assignee": None, "sentiment": "negative", "reply_count": 0,
         "escalation_reason": None, "resolution_note": None},
        {"id": "CASE-5104", "subject": "Wants to change the shipping address", "customer_email": "grace@example.com",
         "order_ref": "ORD-2046", "priority": "low", "status": "pending", "channel": "chat",
         "opened_at": "2026-11-06T18:20:00Z", "last_reply_at": "2026-11-07T08:05:00Z", "age_hours": 28,
         "assignee": "AG-1", "sentiment": "neutral", "reply_count": 1,
         "escalation_reason": None, "resolution_note": None},
        {"id": "CASE-5105", "subject": "Monitor order four days late", "customer_email": "mateo@example.com",
         "order_ref": "ORD-2047", "priority": "urgent", "status": "escalated", "channel": "phone",
         "opened_at": "2026-11-02T09:00:00Z", "last_reply_at": "2026-11-07T11:30:00Z", "age_hours": 140,
         "assignee": "AG-3", "sentiment": "negative", "reply_count": 7,
         "escalation_reason": "VIP customer, repeat delay.", "resolution_note": None},
        {"id": "CASE-5106", "subject": "Invoice copy requested", "customer_email": "tobi@example.com",
         "order_ref": "ORD-2044", "priority": "low", "status": "resolved", "channel": "email",
         "opened_at": "2026-11-05T10:00:00Z", "last_reply_at": "2026-11-05T12:40:00Z", "age_hours": 50,
         "assignee": "AG-2", "sentiment": "positive", "reply_count": 2,
         "escalation_reason": None, "resolution_note": "Invoice emailed."},
        {"id": "CASE-5107", "subject": "Charged twice for one order", "customer_email": "priya@example.com",
         "order_ref": "ORD-2050", "priority": "high", "status": "open", "channel": "email",
         "opened_at": "2026-11-06T07:15:00Z", "last_reply_at": "2026-11-07T09:20:00Z", "age_hours": 52,
         "assignee": "AG-3", "sentiment": "negative", "reply_count": 3,
         "escalation_reason": None, "resolution_note": None},
        {"id": "CASE-5108", "subject": "Product question before buying", "customer_email": "sara@example.com",
         "order_ref": None, "priority": "low", "status": "new", "channel": "chat",
         "opened_at": "2026-11-07T13:00:00Z", "last_reply_at": None, "age_hours": 18,
         "assignee": None, "sentiment": "positive", "reply_count": 0,
         "escalation_reason": None, "resolution_note": None},
        {"id": "CASE-5109", "subject": "Refund not received", "customer_email": "noah@example.com",
         "order_ref": "ORD-2049", "priority": "normal", "status": "pending", "channel": "email",
         "opened_at": "2026-11-03T16:10:00Z", "last_reply_at": "2026-11-05T08:00:00Z", "age_hours": 110,
         "assignee": "AG-1", "sentiment": "negative", "reply_count": 3,
         "escalation_reason": None, "resolution_note": None},
    ),
)

AGENTS = EntitySpec(
    name="agents",
    label="Support agents",
    description="The support team and its current load.",
    title_field="name",
    fields=(
        FieldSpec("id", "id", "Agent", role="key"),
        FieldSpec("name", "string", "Name", role="title"),
        FieldSpec("team", "enum", "Team", values=("frontline", "escalations", "billing"), role="status"),
        FieldSpec("open_cases", "int", "Open cases", role="metric"),
        FieldSpec("shift", "enum", "Shift", values=("morning", "evening", "night")),
    ),
    seed=(
        {"id": "AG-1", "name": "Lena Petrova", "team": "frontline", "open_cases": 3, "shift": "morning"},
        {"id": "AG-2", "name": "Omar Said", "team": "frontline", "open_cases": 2, "shift": "evening"},
        {"id": "AG-3", "name": "Jade Chen", "team": "escalations", "open_cases": 2, "shift": "morning"},
        {"id": "AG-4", "name": "Ruben Castro", "team": "billing", "open_cases": 0, "shift": "night"},
    ),
)

SPEC = AppSpec(
    id="helpdesk",
    label="Helpdesk",
    vendor="Lighthouse Support Cloud",
    description="Customer support desk: cases, priorities, assignments and resolutions.",
    entities=(CASES, AGENTS),
    token_env="MORPH_HELPDESK_TOKEN",
    port=8812,
    accent="#0e7490",
)

app = build_app(SPEC)
