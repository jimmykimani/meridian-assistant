"""
Simulated shopper dialogue with a mocked LLM and MCP.

Covers every Meridian tool name (public, verify, sensitive) without Groq or a live MCP server.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from meridian_support.agent import MeridianAgent
from meridian_support.configs.settings import Settings
from meridian_support.services.sessions import SessionManager

CUST_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
ORDER_ID = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
NEW_ORDER_ID = "c3d4e5f6-a7b8-9012-cdef-123456789012"


def _test_settings() -> Settings:
    return Settings(
        groq_api_key="test-key-not-used",
        groq_model="llama-3.1-8b-instant",
        mcp_server_url="http://unused.test",
        max_tool_rounds=8,
        max_history_turns=12,
        groq_completion_retries=2,
        groq_retry_base_sec=0.01,
        max_tool_result_chars=8000,
        max_assistant_chars_in_context=8000,
    )


def _tool_call(name: str, args: dict[str, Any] | None = None, *, call_id: str | None = None) -> MagicMock:
    tc = MagicMock()
    tc.id = call_id or f"id_{name}"
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(args or {})
    return tc


def _resp_tools(*calls: MagicMock) -> MagicMock:
    msg = MagicMock()
    msg.content = None
    msg.tool_calls = list(calls)
    ch = MagicMock()
    ch.message = msg
    out = MagicMock()
    out.choices = [ch]
    return out


def _resp_text(text: str) -> MagicMock:
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = []
    ch = MagicMock()
    ch.message = msg
    out = MagicMock()
    out.choices = [ch]
    return out


class FakeMCP:
    """Minimal MCP stand-in: records tools/call and returns canned payloads."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self, *, use_cache: bool = True) -> list[dict[str, Any]]:
        return [
            {"name": n}
            for n in (
                "list_products",
                "search_products",
                "get_product",
                "verify_customer_pin",
                "get_customer",
                "list_orders",
                "get_order",
                "create_order",
            )
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        args = dict(arguments or {})
        self.calls.append((name, args))
        if name == "search_products":
            return (
                "1. [KEY-0001] Meridian Wireless Keyboard — Price: $79.99 | Stock: 12 units\n"
                "2. [KEY-0002] Compact Keyboard — Price: $49.00 | Stock: 0 units (out of stock)"
            )
        if name == "list_products":
            return (
                "1. [COM-0001] 24in Monitor — $149.00 | 8 units\n"
                "2. [COM-0002] 27in Monitor — $199.00 | 3 units"
            )
        if name == "get_product":
            sku = args.get("sku", "KEY-0001")
            return {
                "sku": sku,
                "name": "Meridian Wireless Keyboard",
                "unit_price": "79.99",
                "currency": "USD",
                "stock_units": 12,
            }
        if name == "verify_customer_pin":
            return {"customer_id": CUST_ID, "message": "Verified for tests."}
        if name == "list_orders":
            return f"Order [{ORDER_ID}] status=submitted total=79.99 USD"
        if name == "get_order":
            return {
                "order_id": ORDER_ID,
                "status": "submitted",
                "total": "79.99",
                "currency": "USD",
            }
        if name == "get_customer":
            return {"customer_id": CUST_ID, "email": "shopper@example.net"}
        if name == "create_order":
            return {
                "order_id": NEW_ORDER_ID,
                "customer_id": CUST_ID,
                "status": "submitted",
                "total": "79.99",
                "currency": "USD",
                "items": [{"sku": "KEY-0001", "quantity": 1, "unit_price": "79.99", "currency": "USD"}],
            }
        return {"error": f"unexpected tool {name!r}"}

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_shopper_conversation_hits_every_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Narrative: browse → product detail → catalog → orders blocked → verify →
    orders → order detail → profile → place order.
    """
    settings = _test_settings()
    mcp = FakeMCP()
    agent = MeridianAgent(settings, mcp)  # type: ignore[arg-type]
    sm = SessionManager()
    session = sm.new_session()

    llm_queue: list[MagicMock] = [
        # 1) search keyboards
        _resp_tools(_tool_call("search_products", {"query": "keyboard"})),
        _resp_text("Here are keyboards from Meridian—KEY-0001 is in stock."),
        # 2) product detail
        _resp_tools(_tool_call("get_product", {"sku": "KEY-0001"})),
        _resp_text("KEY-0001 is $79.99 with 12 units on hand."),
        # 3) list catalog
        _resp_tools(_tool_call("list_products", {"is_active": True})),
        _resp_text("Active monitors and more are listed above."),
        # 4) orders without auth → gate, then assistant explains
        _resp_tools(_tool_call("list_orders", {})),
        _resp_text("Please verify with your Meridian email and PIN to see orders."),
        # 5) verify
        _resp_tools(
            _tool_call(
                "verify_customer_pin",
                {"email": "shopper@example.net", "pin": "4821"},
            )
        ),
        _resp_text("You're verified. I can help with orders and checkout."),
        # 6) list orders (authed)
        _resp_tools(_tool_call("list_orders", {})),
        _resp_text(f"I see order {ORDER_ID} submitted."),
        # 7) order detail
        _resp_tools(_tool_call("get_order", {"order_id": ORDER_ID})),
        _resp_text("That order is still submitted; totals look correct."),
        # 8) customer profile
        _resp_tools(_tool_call("get_customer", {"customer_id": CUST_ID})),
        _resp_text("Your profile on file is shopper@example.net."),
        # 9) create order
        _resp_tools(
            _tool_call(
                "create_order",
                {
                    "customer_id": CUST_ID,
                    "items": [
                        {
                            "sku": "KEY-0001",
                            "quantity": 1,
                            "unit_price": "79.99",
                            "currency": "USD",
                        }
                    ],
                },
            )
        ),
        _resp_text("**Your order has been created successfully!** Receipt in app."),
    ]

    async def _sequenced_completion(*_a: Any, **_k: Any) -> MagicMock:
        if not llm_queue:
            raise AssertionError("LLM mock exhausted too early")
        return llm_queue.pop(0)

    monkeypatch.setattr(agent, "_chat_completion", _sequenced_completion)

    out1 = await agent.chat(session, "I'm shopping for keyboards—what do you have?")
    assert "keyboard" in out1["message"].lower()
    assert out1["tool_used"] == "search_products"

    out2 = await agent.chat(session, "Tell me everything about SKU KEY-0001")
    assert "KEY-0001" in out2["message"]
    assert out2["tool_used"] == "get_product"

    out3 = await agent.chat(session, "Show active products in the catalog briefly")
    assert out3["tool_used"] == "list_products"

    out4 = await agent.chat(session, "What are my recent orders?")
    assert out4.get("requires_auth") is True
    assert out4["tool_used"] == "list_orders"

    assert session.authenticated_customer_id is None
    out5 = await agent.chat(
        session,
        "My email is shopper@example.net and my PIN is 4821",
    )
    assert session.authenticated_customer_id == CUST_ID
    assert out5["tool_used"] == "verify_customer_pin"

    out6 = await agent.chat(session, "List my orders now")
    assert out6["tool_used"] == "list_orders"
    assert ORDER_ID in out6["message"]

    out7 = await agent.chat(session, f"Pull up details for order {ORDER_ID}")
    assert out7["tool_used"] == "get_order"

    out8 = await agent.chat(session, "What's on my customer profile?")
    assert out8["tool_used"] == "get_customer"

    out9 = await agent.chat(
        session,
        "Place an order for one KEY-0001 at list price in USD",
    )
    assert out9["tool_used"] == "create_order"
    assert "success" in out9["message"].lower()

    mcp_names = {name for name, _ in mcp.calls}
    assert mcp_names == {
        "search_products",
        "get_product",
        "list_products",
        "verify_customer_pin",
        "list_orders",
        "get_order",
        "get_customer",
        "create_order",
    }
    # First list_orders was gated — MCP should not have been hit then.
    list_order_calls = [n for n, _ in mcp.calls if n == "list_orders"]
    assert list_order_calls == ["list_orders"]

    await agent.aclose()
