"""Tools exposed to the Hermes model, with no unapproved action executor."""

CATALOG = {
    "description": "Discover sources, fields, available actions and permissions connected to MORPH. Read this before designing an adaptive workspace.",
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False
    },
}

DESIGN = {
    "description": "Design a new MORPH workspace for the user's goal using Hermes reasoning and connected data schemas. Saves a validated, declarative interface rendered in the Hermes dashboard. No application mutation occurs.",
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "The user's intended workflow, e.g. focus on delayed orders",
                "maxLength": 400
            }
        },
        "required": ["goal"],
        "additionalProperties": False
    },
}

STATE = {
    "description": "Inspect the current MORPH workspace, live data snapshots, pending approvals, and verified action history.",
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False
    },
}

PROPOSE = {
    "description": "Prepare an authorized action against a connected demo application. Never executes it. The human must approve the pending operation in the MORPH dashboard; this agent has no approval/execution tool.",
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "enum": ["orders", "support", "inventory"]
            },
            "record_id": {"type": "string"},
            "operation": {
                "type": "string",
                "enum": [
                    "flag_for_review",
                    "mark_followed_up",
                    "resolve_case",
                    "escalate_case",
                    "request_restock"
                ]
            }
        },
        "required": ["source", "record_id", "operation"],
        "additionalProperties": False
    },
}

DESIGN_RESULT = {
    "type": "object",
    "properties": {
        "widgets": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["metric", "table"]
                    },
                    "source": {"type": "string"},
                    "title": {"type": "string"},
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "aggregation": {
                        "type": "string",
                        "enum": ["count", "sum"]
                    },
                    "field": {"type": "string"},
                    "where": {
                        "type": "object",
                        "additionalProperties": {
                            "type": ["string", "number", "boolean"]
                        }
                    }
                },
                "required": ["kind", "source", "title"],
                "additionalProperties": False
            }
        }
    },
    "required": ["widgets"],
    "additionalProperties": False
}
