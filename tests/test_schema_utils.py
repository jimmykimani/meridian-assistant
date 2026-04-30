from __future__ import annotations

from meridian_support.schema_utils import simplify_schema_for_gemini


def test_simplify_anyof_prefers_string_branch() -> None:
    schema = {
        "type": "object",
        "properties": {
            "category": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Category",
            }
        },
        "title": "list_productsArguments",
    }
    out = simplify_schema_for_gemini(schema)
    assert out["properties"]["category"]["type"] == "string"
    assert "title" not in out


def test_simplify_nested_object() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object", "properties": {"sku": {"type": "string"}}},
            }
        },
        "required": ["items"],
    }
    out = simplify_schema_for_gemini(schema)
    assert out["required"] == ["items"]
    assert out["properties"]["items"]["type"] == "array"
