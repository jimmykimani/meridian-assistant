#!/usr/bin/env python3
"""Phase 1 helper: call initialize + tools/list and print JSON summary."""

from __future__ import annotations

import json
import os
import urllib.request

MCP_URL = os.environ.get(
    "MCP_SERVER_URL",
    "https://order-mcp-74afyau24q-uc.a.run.app/mcp",
)

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def post(payload: dict) -> tuple[str, dict]:
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode(),
        headers=HEADERS,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        content_type = resp.headers.get("Content-Type", "")
        body = resp.read().decode()
    if "text/event-stream" in content_type.lower():
        data_line = None
        for line in body.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw and raw != "[DONE]":
                    data_line = raw
        if not data_line:
            raise RuntimeError("SSE response but no data line parsed")
        return content_type, json.loads(data_line)
    return content_type, json.loads(body)


def main() -> None:
    _, init = post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "discover_mcp", "version": "0.1.0"},
            },
        }
    )
    print("initialize:", json.dumps(init, indent=2)[:2000])

    _, listed = post(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
    )
    tools = listed.get("result", {}).get("tools", [])
    summary = [
        {
            "name": t.get("name"),
            "description": (t.get("description") or "")[:200],
            "required": list(
                (t.get("inputSchema") or {}).get("required") or []
            ),
        }
        for t in tools
    ]
    print("tools:", json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
