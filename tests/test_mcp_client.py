from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from meridian_support.tools.mcp_client import (
    MAX_FAILURES,
    CircuitBreaker,
    MCPClient,
    reset_mcp_client_singleton,
)


def test_parse_sse_first_data_line() -> None:
    body = """event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n"""
    out = MCPClient._parse_sse(body)
    assert out["jsonrpc"] == "2.0"
    assert out["result"] == {}


def test_parse_response_body_plain_json() -> None:
    out = MCPClient._parse_response_body(
        "application/json", '{"jsonrpc":"2.0","id":1,"result":{}}'
    )
    assert out["result"] == {}


def test_tool_result_to_value_text_json() -> None:
    raw = {
        "result": {
            "content": [{"type": "text", "text": '{"ok": true}'}],
        }
    }
    assert MCPClient._tool_result_to_value(raw) == {"ok": True}


def test_tool_result_to_value_plain_text() -> None:
    raw = {"result": {"content": [{"type": "text", "text": "hello"}]}}
    assert MCPClient._tool_result_to_value(raw) == "hello"


def test_tool_result_jsonrpc_error() -> None:
    raw = {"error": {"code": -32600, "message": "bad"}}
    with pytest.raises(RuntimeError, match="bad"):
        MCPClient._tool_result_to_value(raw)


@pytest.mark.asyncio
async def test_circuit_opens_after_http_errors() -> None:
    reset_mcp_client_singleton()
    client = MCPClient(url="https://example.invalid-mcp.test/nope", retries=1)

    with patch.object(client, "_get_http", new_callable=AsyncMock) as mock_get:
        http = AsyncMock()
        mock_get.return_value = http

        err = httpx.HTTPStatusError(
            "fail",
            request=httpx.Request("POST", client.url),
            response=httpx.Response(500, request=httpx.Request("POST", client.url)),
        )
        http.post = AsyncMock(side_effect=err)

        for _ in range(MAX_FAILURES):
            with pytest.raises(RuntimeError):
                await client._post({"jsonrpc": "2.0", "id": 1, "method": "ping"})

        assert client.circuit.is_open()

    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_tools_live() -> None:
    client = MCPClient()
    try:
        tools = await client.list_tools(use_cache=False)
        names = {t["name"] for t in tools if "name" in t}
        assert "search_products" in names
        assert "verify_customer_pin" in names
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_call_tool_search_live() -> None:
    client = MCPClient()
    try:
        out = await client.call_tool("search_products", {"query": "monitor"})
        assert out is not None
        if isinstance(out, str):
            assert len(out) > 0
    finally:
        await client.aclose()
