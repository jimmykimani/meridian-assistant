"""Normalize MCP JSON Schema for Gemini function declarations."""

from __future__ import annotations

import copy
from typing import Any


def simplify_schema_for_gemini(schema: Any) -> dict[str, Any]:
    """Return a JSON Schema object Gemini tolerates (drops anyOf/null unions, titles)."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    root = copy.deepcopy(schema)
    if root.get("type") != "object":
        root = {"type": "object", "properties": {}, "required": []}
    props = root.get("properties")
    if isinstance(props, dict):
        new_props: dict[str, Any] = {}
        for key, val in props.items():
            new_props[key] = _simplify_property(val)
        root["properties"] = new_props
    root.pop("title", None)
    root.pop("$defs", None)
    req = root.get("required")
    if isinstance(req, list):
        root["required"] = [r for r in req if isinstance(r, str)]
    else:
        root["required"] = []
    return root


def _simplify_property(val: Any) -> dict[str, Any]:
    if not isinstance(val, dict):
        return {"type": "string"}
    v = copy.deepcopy(val)
    if "anyOf" in v and isinstance(v["anyOf"], list):
        non_null = [x for x in v["anyOf"] if isinstance(x, dict) and x.get("type") != "null"]
        if len(non_null) == 1:
            base = dict(non_null[0])
        elif non_null:
            # Prefer string if any branch is string
            str_opts = [x for x in non_null if x.get("type") == "string"]
            base = dict(str_opts[0]) if str_opts else dict(non_null[0])
        else:
            base = {"type": "string"}
        desc = v.get("description")
        if desc and "description" not in base:
            base["description"] = desc
        base.pop("title", None)
        return base
    v.pop("title", None)
    if v.get("type") == "object" and isinstance(v.get("properties"), dict):
        v["properties"] = {k: _simplify_property(p) for k, p in v["properties"].items()}
    if v.get("type") == "array" and isinstance(v.get("items"), dict):
        v["items"] = _simplify_property(v["items"])
    return v
