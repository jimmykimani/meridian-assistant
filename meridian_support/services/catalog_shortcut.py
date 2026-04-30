"""Direct MCP catalog replies for high-volume intents (bypasses Groq)."""

from __future__ import annotations

import logging
import os
from typing import Any

from meridian_support.catalog.intents import format_catalog_message, should_list_products_shortcut
from meridian_support.guardrails.injection import detect_prompt_injection
from meridian_support.services.sessions import SessionState
from meridian_support.tools.mcp_client import MCPClient

logger = logging.getLogger(__name__)


async def try_serve_catalog_shortcut(
    mcp: MCPClient,
    session: SessionState,
    user_text: str,
) -> dict[str, Any] | None:
    """
    For full-catalog or broad browse asks, call MCP list_products directly (no Groq).

    Avoids HTTP 413 and avoids flaky LLM paths for simple listing.
    """
    if detect_prompt_injection(user_text):
        return None
    if not should_list_products_shortcut(user_text):
        return None
    max_display = max(10_000, int(os.environ.get("MERIDIAN_CATALOG_RESPONSE_MAX_CHARS", "250000")))
    try:
        raw = await mcp.call_tool("list_products", {"is_active": True})
    except Exception as exc:
        logger.warning("catalog shortcut MCP call failed: %s", exc)
        msg = (
            "We couldn’t reach the product catalog just now. Please try again in a moment, "
            "or ask for a **category** (monitors, keyboards, computers, …)."
        )
    else:
        msg = format_catalog_message(raw, max_chars=max_display)
    session.conversation = [
        *session.conversation,
        {"role": "user", "content": user_text.strip()},
        {"role": "assistant", "content": msg},
    ]
    return {
        "message": msg,
        "requires_auth": False,
        "tool_used": "list_products",
        "confidence": 0.99,
    }
