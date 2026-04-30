from __future__ import annotations

import os

import pytest

from agent import MeridianAgent
from mcp_client import MCPClient
from session_manager import SessionManager
from settings import Settings


pytestmark = pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="Set GROQ_API_KEY to run Groq+MCP integration tests",
)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_chat_uses_search_products() -> None:
    settings = Settings.from_env()
    mcp = MCPClient(url=settings.mcp_server_url)
    agent = MeridianAgent(settings, mcp)
    sm = SessionManager()
    session = sm.new_session()
    try:
        out = await agent.chat(session, "Search the catalog for monitors and summarize briefly.")
        assert isinstance(out, dict)
        assert "message" in out
        assert "requires_auth" in out
        assert "tool_used" in out
        assert "confidence" in out
        assert isinstance(out["message"], str) and len(out["message"]) > 0
    finally:
        await agent.aclose()
        await mcp.aclose()
