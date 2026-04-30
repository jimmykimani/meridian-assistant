"""
Resilient MCP client for Meridian order MCP (Streamable HTTP).

- Tool discovery (tools/list) with optional cache
- tools/call with retries and exponential backoff
- Circuit breaker after repeated failures
- JSON or SSE responses; structured errors for callers (no raw exceptions to UI)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "https://order-mcp-74afyau24q-uc.a.run.app/mcp"
MAX_FAILURES = 3
BACKOFF_BASE = 0.5
DEFAULT_TIMEOUT = 120.0
DEFAULT_RETRIES = 3

JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


@dataclass
class CircuitBreaker:
    failures: int = 0
    opened_at: float = 0.0
    reset_after: float = 30.0

    def is_open(self) -> bool:
        if self.failures >= MAX_FAILURES:
            if time.time() - self.opened_at > self.reset_after:
                self.failures = 0
                return False
            return True
        return False

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= MAX_FAILURES:
            self.opened_at = time.time()
            logger.error("MCP circuit breaker OPENED")

    def record_success(self) -> None:
        if self.failures > 0:
            logger.info("MCP circuit breaker reset after success")
        self.failures = 0


class MCPClient:
    """Async JSON-RPC client for Streamable HTTP MCP endpoint."""

    def __init__(
        self,
        url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.url = url or os.environ.get("MCP_SERVER_URL", DEFAULT_MCP_URL)
        self.timeout = timeout
        self.retries = retries
        self.circuit = CircuitBreaker()
        self._msg_id = 0
        self._tools: list[dict[str, Any]] | None = None
        self._http: httpx.AsyncClient | None = None

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=30.0),
                follow_redirects=True,
            )
        return self._http

    @staticmethod
    def _parse_sse(text: str) -> dict[str, Any]:
        """Extract first JSON object from SSE data lines."""
        for line in text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    try:
                        return json.loads(data)
                    except json.JSONDecodeError:
                        continue
        raise RuntimeError(f"Could not parse SSE response: {text[:400]!r}")

    @staticmethod
    def _parse_response_body(content_type: str, text: str) -> dict[str, Any]:
        ct = (content_type or "").lower()
        if "text/event-stream" in ct or text.lstrip().startswith("data:"):
            return MCPClient._parse_sse(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON from MCP: {e}; body={text[:400]!r}") from e

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.circuit.is_open():
            raise RuntimeError(
                "MCP server is temporarily unavailable. Please try again in about 30 seconds."
            )

        http = await self._get_http()

        for attempt in range(self.retries):
            try:
                resp = await http.post(self.url, json=payload, headers=JSON_HEADERS)
                resp.raise_for_status()
                result = self._parse_response_body(
                    resp.headers.get("content-type", ""),
                    resp.text,
                )
                self.circuit.record_success()
                return result

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                body_preview = (e.response.text or "")[:200]

                if status == 403:
                    raise RuntimeError(
                        "MCP server rejected this host (403). The MCP endpoint may require "
                        "an allowlisted deployment region or IP — check with the backend team "
                        f"for your hosting provider. Response: {body_preview}"
                    ) from e

                if status >= 500 and attempt < self.retries - 1:
                    wait = BACKOFF_BASE * (2**attempt)
                    logger.warning("MCP %s, retry %s in %ss", status, attempt + 1, wait)
                    await asyncio.sleep(wait)
                    continue

                self.circuit.record_failure()
                raise RuntimeError(f"MCP HTTP {status}: {body_preview}") from e

            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
                if attempt < self.retries - 1:
                    wait = BACKOFF_BASE * (2**attempt)
                    logger.warning("MCP network error, retry %s in %ss: %s", attempt + 1, wait, e)
                    await asyncio.sleep(wait)
                    continue
                self.circuit.record_failure()
                raise RuntimeError(f"Cannot reach MCP server: {e}") from e

            except RuntimeError as e:
                self.circuit.record_failure()
                raise

            except Exception as e:
                logger.exception("Unexpected MCP error")
                self.circuit.record_failure()
                raise RuntimeError(f"MCP request failed: {e}") from e

        raise RuntimeError("MCP call failed after retries")

    async def initialize(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "meridian-chatbot", "version": "1.0.0"},
            },
        }
        return await self._post(payload)

    async def list_tools(self, *, use_cache: bool = True) -> list[dict[str, Any]]:
        if use_cache and self._tools is not None:
            return self._tools

        try:
            await self.initialize()
        except Exception as exc:
            logger.warning("initialize failed (continuing to tools/list): %s", exc)

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        }
        result = await self._post(payload)
        tools = result.get("result", {}).get("tools", [])
        if not isinstance(tools, list):
            tools = []
        self._tools = tools
        names = [t.get("name") for t in tools if isinstance(t, dict)]
        logger.info("Discovered %s MCP tools: %s", len(tools), names)
        return tools

    def invalidate_tools_cache(self) -> None:
        self._tools = None

    @staticmethod
    def _tool_result_to_value(result: dict[str, Any]) -> Any:
        if "error" in result:
            err = result["error"]
            code = err.get("code")
            msg = err.get("message", "")
            raise RuntimeError(f"MCP error {code}: {msg}")

        tool_result = result.get("result") or {}
        content = tool_result.get("content")
        if not content:
            return None

        texts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if t:
                    texts.append(t)
        combined = "\n".join(texts)
        if not combined:
            return None

        try:
            return json.loads(combined)
        except (json.JSONDecodeError, TypeError, ValueError):
            return combined

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        result = await self._post(payload)
        return self._tool_result_to_value(result)


_client: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    global _client
    if _client is None:
        _client = MCPClient()
    return _client


def reset_mcp_client_singleton() -> None:
    """Test helper: clear process-wide singleton."""
    global _client
    _client = None
