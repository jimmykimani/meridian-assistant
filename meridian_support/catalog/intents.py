"""Catalog intent detection and formatting for MCP shortcuts (no LLM)."""

from __future__ import annotations

import json
import re
from typing import Any


def is_show_all_catalog_request(text: str) -> bool:
    """True when the shopper clearly wants the full / entire active catalog."""
    raw = text.strip().lower()
    if not raw:
        return False
    t = re.sub(r"[!?.…]+", " ", raw)
    t = re.sub(r"\s+", " ", t).strip()
    phrases = (
        "show all products",
        "show me all products",
        "list all products",
        "list every product",
        "all products",
        "every product",
        "full product catalog",
        "full catalog",
        "complete catalog",
        "entire catalog",
        "all items",
        "show everything you sell",
        "show everything",
        "show all items",
        "complete product list",
        "entire product list",
    )
    for p in phrases:
        if t == p or t.startswith(p + " ") or t.endswith(" " + p):
            return True
    return any(p in t for p in phrases)


def is_browse_catalog_request(text: str) -> bool:
    """Broad product browse without a SKU (e.g. 'show me products')."""
    raw = text.strip().lower()
    if not raw:
        return False
    t = re.sub(r"[!?.…]+", " ", raw)
    t = re.sub(r"\s+", " ", t).strip()
    phrases = (
        "show me products",
        "show products",
        "list products",
        "what products do you have",
        "what products",
        "products do you have",
        "what do you sell",
        "browse products",
        "see your products",
        "view products",
        "product list",
        "catalog",
    )
    if any(t == p or t.startswith(p + " ") for p in phrases):
        return True
    return any(p in t for p in phrases)


def should_list_products_shortcut(text: str) -> bool:
    """Use direct MCP list_products for this message (skips Groq)."""
    return is_show_all_catalog_request(text) or is_browse_catalog_request(text)


def format_catalog_message(
    raw: Any,
    *,
    max_chars: int,
    heading: str = "**Active products**",
) -> str:
    """Turn MCP list_products payload into markdown for the user."""
    body = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    if len(body) > max_chars:
        body = (
            body[:max_chars]
            + "\n\n_(Catalog hits the display limit here; ask by **category** or **search** for the rest.)_"
        )
    return f"{heading}\n\n{body}"
