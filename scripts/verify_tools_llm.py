#!/usr/bin/env python3
"""
End-to-end local check: Groq orchestrates each MCP tool class (public + verify + gated).

Requires repo-root `.env` with GROQ_API_KEY. Hits real MCP (cold start can be 30–90s).

Usage (from repo root):
  python scripts/verify_tools_llm.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from meridian_support.agent import MeridianAgent  # noqa: E402
from meridian_support.mcp_client import MCPClient  # noqa: E402
from meridian_support.session_manager import SessionManager  # noqa: E402
from meridian_support.settings import Settings  # noqa: E402


def _fail(name: str, out: dict, extra: str = "") -> None:
    print(f"FAIL {name}: {json.dumps(out, indent=2)[:2000]}{extra}", file=sys.stderr)
    raise SystemExit(1)


async def run() -> None:
    settings = Settings.from_env()
    mcp = MCPClient(url=settings.mcp_server_url)
    agent = MeridianAgent(settings, mcp)
    sm = SessionManager()

    async def chat(session_msg: tuple[str, str], session) -> dict:
        label, text = session_msg
        print(f"\n--- {label} ---\n{text[:120]}…" if len(text) > 120 else f"\n--- {label} ---\n{text}")
        out = await agent.chat(session, text)
        msg = out.get("message", "")
        if "413" in msg or "too large" in msg.lower():
            _fail(label, out, "\n(Groq payload / 413)")
        if not isinstance(msg, str) or not msg.strip():
            _fail(label, out, "\n(empty assistant message)")
        print(f"tool_used={out.get('tool_used')!r} requires_auth={out.get('requires_auth')}")
        print(msg[:500] + ("…" if len(msg) > 500 else ""))
        return out

    try:
        # 1) Public: list_products (explicit so model calls tool)
        s1 = sm.new_session()
        out1 = await chat(
            (
                "list_products",
                "Use the list_products tool with is_active true (no category) and summarize 3 product names from the result.",
            ),
            s1,
        )
        if out1.get("tool_used") != "list_products":
            print(
                f"WARN list_products: expected tool_used list_products, got {out1.get('tool_used')!r}",
                file=sys.stderr,
            )

        # 2) Public: search_products
        s2 = sm.new_session()
        out2 = await chat(
            (
                "search_products",
                'Use search_products with query "monitor" and list two matching SKUs if any.',
            ),
            s2,
        )
        if out2.get("tool_used") != "search_products":
            print(
                f"WARN search_products: expected tool_used search_products, got {out2.get('tool_used')!r}",
                file=sys.stderr,
            )

        # 3) Public: get_product (SKU from assessment docs)
        s3 = sm.new_session()
        out3 = await chat(
            (
                "get_product",
                "Use get_product with sku COM-0001 and say whether it is in stock in one sentence.",
            ),
            s3,
        )
        if out3.get("tool_used") != "get_product":
            print(
                f"WARN get_product: expected tool_used get_product, got {out3.get('tool_used')!r}",
                file=sys.stderr,
            )

        # 4) verify_customer_pin (Groq must call verify tool)
        s4 = sm.new_session()
        out4 = await chat(
            (
                "verify_customer_pin",
                "Verify Meridian customer email donaldgarcia@example.net with PIN 7912 using verify_customer_pin.",
            ),
            s4,
        )
        if out4.get("tool_used") != "verify_customer_pin":
            print(
                f"WARN verify: expected tool_used verify_customer_pin, got {out4.get('tool_used')!r}",
                file=sys.stderr,
            )
        if not s4.authenticated_customer_id:
            _fail("verify_customer_pin (session not authed)", out4)

        # 5–7) Authenticated: same session — list_orders, get_customer
        out5 = await chat(
            ("list_orders", "List my recent orders using list_orders. One short sentence."),
            s4,
        )
        if out5.get("tool_used") != "list_orders":
            print(
                f"WARN list_orders: expected tool_used list_orders, got {out5.get('tool_used')!r}",
                file=sys.stderr,
            )

        out6 = await chat(
            ("get_customer", "Show my customer profile using get_customer. One sentence."),
            s4,
        )
        if out6.get("tool_used") != "get_customer":
            print(
                f"WARN get_customer: expected tool_used get_customer, got {out6.get('tool_used')!r}",
                file=sys.stderr,
            )

        # 8) get_order — need an order id from list_orders text; ask model to chain
        out7 = await chat(
            (
                "get_order",
                "From my orders, pick the first order id (UUID) you can see and call get_order with that order_id. "
                "If none, say no orders.",
            ),
            s4,
        )
        if out7.get("tool_used") not in ("get_order", None):
            pass
        if out7.get("tool_used") != "get_order":
            print(
                f"WARN get_order: tool_used={out7.get('tool_used')!r} (may be OK if no orders)",
                file=sys.stderr,
            )

        # create_order is intentionally not auto-called here (would mutate MCP data). Exercise it manually in UI.

        print("\nOK — Groq + MCP rounds completed without 413/empty failure. You can test in the Streamlit UI.")
    finally:
        await agent.aclose()
        await mcp.aclose()


if __name__ == "__main__":
    asyncio.run(run())
