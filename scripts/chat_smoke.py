#!/usr/bin/env python3
"""One-off async smoke test: Groq + MCP agent (requires GROQ_API_KEY in env)."""

from __future__ import annotations

import argparse
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


async def main() -> None:
    p = argparse.ArgumentParser(description="Run one MeridianAgent.chat turn")
    p.add_argument("message", nargs="?", default="Search the catalog for monitors.")
    args = p.parse_args()

    settings = Settings.from_env()
    mcp = MCPClient(url=settings.mcp_server_url)
    agent = MeridianAgent(settings, mcp)
    sm = SessionManager()
    session = sm.new_session()
    try:
        out = await agent.chat(session, args.message)
        print(json.dumps(out, indent=2))
    finally:
        await agent.aclose()
        await mcp.aclose()


if __name__ == "__main__":
    asyncio.run(main())
