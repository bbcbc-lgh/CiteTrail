"""JSON schemas handed to providers that support structured output.

Keeping them next to the prompts means the instruction text and the enforced
shape cannot drift apart.
"""

PLAN_SCHEMA: dict = {
    "name": "research_plan",
    "schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "intent": {"type": "string"},
                        "query": {"type": "string"},
                    },
                    "required": ["title", "intent", "query"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["tasks"],
        "additionalProperties": False,
    },
}

SUMMARY_SCHEMA: dict = {
    "name": "task_summary",
    "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "cites": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["text", "cites"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "claims"],
        "additionalProperties": False,
    },
}

QUERY_SCHEMA: dict = {
    "name": "probe_queries",
    "schema": {
        "type": "object",
        "properties": {
            "queries": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["queries"],
        "additionalProperties": False,
    },
}
