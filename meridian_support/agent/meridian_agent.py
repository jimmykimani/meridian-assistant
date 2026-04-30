"""
Meridian support agent: Groq (Llama) tool-calling + MCP execution + auth gating.

All business facts come from MCP tools discovered at runtime (no hardcoded tool list
for behavior — only auth routing constants).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from openai import APIStatusError, AsyncOpenAI, RateLimitError

from meridian_support.configs.settings import Settings
from meridian_support.guardrails.injection import detect_prompt_injection
from meridian_support.llm.tracing import wrap_openai_client_for_tracing
from meridian_support.services.sessions import SessionState
from meridian_support.tools.mcp_client import MCPClient

logger = logging.getLogger(__name__)


# Tools we expose to the LLM, in a stable order (subset of MCP server tools).
_MERIDIAN_TOOL_ORDER: tuple[str, ...] = (
    "list_products",
    "search_products",
    "get_product",
    "verify_customer_pin",
    "get_customer",
    "list_orders",
    "get_order",
    "create_order",
)

# Minimal OpenAI-style function declarations (tiny schemas vs. full MCP).
_COMPACT_OPENAI_TOOLS: dict[str, dict[str, Any]] = {
    "list_products": {
        "type": "function",
        "function": {
            "name": "list_products",
            "description": (
                "List catalog products with prices and stock. Use for browse requests "
                '("show products", by category) without needing a SKU first. '
                "For huge catalogs, **always** pass a `category` filter or use `search_products` "
                "so results stay focused—never pull the entire catalog in one call if it can be narrowed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Filter by category"},
                    "is_active": {
                        "type": "boolean",
                        "description": "If true, only active products",
                    },
                },
            },
        },
    },
    "search_products": {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Keyword search over the product catalog.",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
            },
        },
    },
    "get_product": {
        "type": "function",
        "function": {
            "name": "get_product",
            "description": "Get one product by SKU including stock.",
            "parameters": {
                "type": "object",
                "required": ["sku"],
                "properties": {
                    "sku": {"type": "string", "description": "Product SKU, e.g. COM-0001"},
                },
            },
        },
    },
    "verify_customer_pin": {
        "type": "function",
        "function": {
            "name": "verify_customer_pin",
            "description": (
                "Verify Meridian customer with email + 4-digit PIN. "
                "Call only after the user provides both; never echo the PIN."
            ),
            "parameters": {
                "type": "object",
                "required": ["email", "pin"],
                "properties": {
                    "email": {"type": "string"},
                    "pin": {"type": "string", "description": "4-digit PIN"},
                },
            },
        },
    },
    "get_customer": {
        "type": "function",
        "function": {
            "name": "get_customer",
            "description": "Customer profile by UUID (authenticated customers only).",
            "parameters": {
                "type": "object",
                "required": ["customer_id"],
                "properties": {
                    "customer_id": {"type": "string", "description": "Customer UUID"},
                },
            },
        },
    },
    "list_orders": {
        "type": "function",
        "function": {
            "name": "list_orders",
            "description": "List orders; optional filters by customer_id and status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "description": "draft|submitted|approved|fulfilled|cancelled",
                    },
                },
            },
        },
    },
    "get_order": {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Order detail by order UUID.",
            "parameters": {
                "type": "object",
                "required": ["order_id"],
                "properties": {
                    "order_id": {"type": "string", "description": "Order UUID"},
                },
            },
        },
    },
    "create_order": {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": "Create a new order for the verified customer.",
            "parameters": {
                "type": "object",
                "required": ["customer_id", "items"],
                "properties": {
                    "customer_id": {"type": "string"},
                    "items": {
                        "type": "array",
                        "description": "Line items with sku, quantity, unit_price, currency",
                        "items": {
                            "type": "object",
                            "required": ["sku", "quantity", "unit_price", "currency"],
                            "properties": {
                                "sku": {"type": "string"},
                                "quantity": {"type": "integer"},
                                "unit_price": {
                                    "type": "string",
                                    "description": "Decimal as string",
                                },
                                "currency": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}

SYSTEM_INSTRUCTION = """You are Meridian Support for Meridian Electronics (monitors, keyboards, printers, networking, accessories). Sound warm, helpful, and a bit like a knowledgeable store associate—not stiff or robotic.

**Catalog (always use tools for product facts):**
- Call **list_products**, **search_products**, or **get_product** before answering anything about inventory, prices, or SKUs. For vague asks ("show products", "what's good", a category), use **list_products** with `is_active=true` and/or **search_products** with a sensible keyword—do not ask for a SKU first unless the catalog is empty or the tool errors.
- When showing multiple products, use a **numbered list** (1., 2., 3., …). For each item (markdown-friendly):
  - Line 1: **`[SKU]` Name** (keep SKU in brackets so it is easy to copy, e.g. `[COM-0012] Gaming Desktop - Model B`).
  - Line 2: **Price:** from tool data (never omit when the tool includes it).
  - Line 3: **Stock:** numeric **units** from tools when present (e.g. `Stock: 53 units`); if count is 0, say **Out of stock** clearly.
- After a list, add one short friendly line: either highlight **1–2 picks** (e.g. strong stock + good value) you can justify from the data, or invite them to ask for a category, compare models, or say a SKU for deep specs. Do not invent discounts or claims tools did not support.
- When the shopper asks for **“everything”**, **“all products”**, or **20+ items at once**, do **not** paste a giant list: show **up to ~15** strong matches (or one **category** at a time), then invite them to name another category or “show more monitors” etc. Use **list_products** with a `category` filter or **search_products** so tool output stays manageable.
- When the tool returns many rows, show **at least 8** and up to **15** in one reply unless the user asked for fewer. Never mention internal limits or truncation.

**Placing an order:**
- If the shopper wants to check out but is not verified yet, ask them—in chat—for **Meridian email** and **4-digit PIN** on separate lines (or clearly labeled). Say you will not repeat the PIN. Optionally remind them they can also use the sidebar **Account — orders & purchases** form.
- As soon as they provide both, call **verify_customer_pin** with those values, then continue with create_order / cart help using tools.
- Never echo or log the PIN in your visible replies.

**Order confirmation (after create_order succeeds):**
- Show a clear success header (e.g. **Your order has been created successfully!**).
- Then a compact **receipt** block: **Order ID**, **Customer ID** (if returned), **Status** / payment state, numbered line items with qty × unit price = line total, then **Total** with currency—using only values returned by tools for this customer.
- End with one friendly line (inventory updated / anything else the tool text says).

Privacy:
- Never mix another customer's data. Only show order/customer UUIDs that belong to the **current verified** session from tool output.

Honesty:
- Never invent SKUs, prices, stock counts, or order IDs. Refunds are out of scope—direct to human support.
- Stay concise but conversational; one short paragraph of guidance after a long list is welcome."""

PUBLIC_TOOLS = frozenset({"list_products", "get_product", "search_products"})
SENSITIVE_TOOLS = frozenset({"get_customer", "list_orders", "get_order", "create_order"})
VERIFY_TOOL = "verify_customer_pin"

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def extract_customer_id_from_tool_result(result: Any) -> str | None:
    if isinstance(result, dict):
        for key in ("customer_id", "id", "customerId"):
            val = result.get(key)
            if isinstance(val, str) and UUID_RE.fullmatch(val.strip()):
                return val.strip().lower()
    if isinstance(result, str):
        m = UUID_RE.search(result)
        if m:
            return m.group(0).lower()
    return None


def _compact_tools_for_llm(mcp_tool_names: set[str]) -> list[dict[str, Any]]:
    """Small tool list + schemas for Groq (avoids HTTP 413 from oversized MCP schemas)."""
    out: list[dict[str, Any]] = []
    for name in _MERIDIAN_TOOL_ORDER:
        if name not in mcp_tool_names:
            continue
        spec = _COMPACT_OPENAI_TOOLS.get(name)
        if spec:
            out.append(copy.deepcopy(spec))
    return out


def _shrink_tool_contents_in_messages(messages: list[dict[str, Any]], max_chars: int) -> None:
    """Last-resort cap on tool role strings so a full request stays under Groq limits."""
    tail = "\n…"
    for m in messages:
        if m.get("role") != "tool":
            continue
        c = m.get("content")
        if not isinstance(c, str) or len(c) <= max_chars:
            continue
        logger.warning("shrinking oversized tool message in flight (%d -> %d)", len(c), max_chars)
        m["content"] = c[: max(200, max_chars - len(tail))] + tail


def _tool_message_content(payload: Any, *, max_chars: int) -> str:
    """Serialize tool output for the next LLM turn; truncate to stay under Groq size limits."""
    if isinstance(payload, (dict, list)):
        raw = json.dumps({"result": payload}, ensure_ascii=False)
        if len(raw) <= max_chars:
            return raw
        text_repr = json.dumps(payload, ensure_ascii=False)
    else:
        text_repr = str(payload)
        raw = json.dumps({"result": text_repr}, ensure_ascii=False)
        if len(raw) <= max_chars:
            return raw
    note = "\n…"
    cap = max(500, max_chars - len(note) - 40)
    logger.warning("truncating tool result for model (>%d chars)", max_chars)
    truncated = text_repr[:cap] + note
    return json.dumps({"result": truncated}, ensure_ascii=False)


def _retry_after_seconds(exc: BaseException) -> float | None:
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _trim_messages(messages: list[dict[str, Any]], max_turns: int) -> list[dict[str, Any]]:
    """Keep system + recent tail so tool/user/assistant chains stay valid."""
    if not messages:
        return messages
    system = messages[0] if messages[0].get("role") == "system" else None
    rest = messages[1:] if system else messages
    cap = max(6, max_turns * 3)
    if len(rest) <= cap:
        trimmed = rest
    else:
        trimmed = rest[-cap:]
    return ([system] + trimmed) if system else trimmed


class MeridianAgent:
    def __init__(self, settings: Settings, mcp: MCPClient) -> None:
        self._settings = settings
        self._mcp = mcp
        raw_client = AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        self._client = wrap_openai_client_for_tracing(raw_client)
        self._model_id = settings.groq_model
        self._openai_tools: list[dict[str, Any]] | None = None
        self._tool_names: set[str] = set()

    async def aclose(self) -> None:
        await self._client.close()

    async def _chat_completion(self, **kwargs: Any) -> Any:
        """Groq free tier often returns 429; wait and retry with exponential backoff."""
        attempts = self._settings.groq_completion_retries
        base = self._settings.groq_retry_base_sec
        last: BaseException | None = None
        for attempt in range(attempts):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except RateLimitError as exc:
                last = exc
                if attempt >= attempts - 1:
                    raise
                wait = _retry_after_seconds(exc)
                if wait is None:
                    wait = min(60.0, base * (2**attempt))
                logger.warning(
                    "Groq rate limited, retry %s/%s after %.1fs",
                    attempt + 1,
                    attempts,
                    wait,
                )
                await asyncio.sleep(wait)
            except APIStatusError as exc:
                if getattr(exc, "status_code", None) != 429:
                    raise
                last = exc
                if attempt >= attempts - 1:
                    raise
                wait = _retry_after_seconds(exc)
                if wait is None:
                    wait = min(60.0, base * (2**attempt))
                logger.warning(
                    "Groq HTTP 429, retry %s/%s after %.1fs",
                    attempt + 1,
                    attempts,
                    wait,
                )
                await asyncio.sleep(wait)
        assert last is not None
        raise last

    async def _create_chat_completion_stream(self, **kwargs: Any) -> Any:
        """Same retry policy as _chat_completion, but returns an async stream from Groq."""
        kwargs = dict(kwargs)
        kwargs["stream"] = True
        attempts = self._settings.groq_completion_retries
        base = self._settings.groq_retry_base_sec
        last: BaseException | None = None
        for attempt in range(attempts):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except RateLimitError as exc:
                last = exc
                if attempt >= attempts - 1:
                    raise
                wait = _retry_after_seconds(exc)
                if wait is None:
                    wait = min(60.0, base * (2**attempt))
                logger.warning(
                    "Groq rate limited (stream), retry %s/%s after %.1fs",
                    attempt + 1,
                    attempts,
                    wait,
                )
                await asyncio.sleep(wait)
            except APIStatusError as exc:
                if getattr(exc, "status_code", None) != 429:
                    raise
                last = exc
                if attempt >= attempts - 1:
                    raise
                wait = _retry_after_seconds(exc)
                if wait is None:
                    wait = min(60.0, base * (2**attempt))
                logger.warning(
                    "Groq HTTP 429 (stream), retry %s/%s after %.1fs",
                    attempt + 1,
                    attempts,
                    wait,
                )
                await asyncio.sleep(wait)
        assert last is not None
        raise last

    @staticmethod
    def _synthetic_response_from_stream(
        content_parts: list[str],
        tool_calls_state: dict[int, dict[str, str]],
    ) -> Any:
        """Build an object shaped like a non-streaming chat completion for shared tool logic."""
        tlist: list[Any] = []
        for idx in sorted(tool_calls_state):
            st = tool_calls_state[idx]
            tid = (st.get("id") or "").strip() or f"stream_{idx}"
            name = (st.get("name") or "").strip()
            args = st.get("arguments") or "{}"
            fn = SimpleNamespace(name=name, arguments=args)
            tlist.append(SimpleNamespace(id=tid, function=fn))
        text = "".join(content_parts) if content_parts else None
        msg = SimpleNamespace(content=text, tool_calls=tlist)
        choice = SimpleNamespace(message=msg, finish_reason="tool_calls" if tlist else "stop")
        return SimpleNamespace(choices=[choice])

    async def _iter_stream_deltas(
        self, stream: Any
    ) -> AsyncIterator[tuple[str, Any]]:
        """
        Consume one streamed completion. Yields ("delta", str) for assistant text,
        then ("response", synthetic_completion) once.
        """
        content_parts: list[str] = []
        tool_calls_state: dict[int, dict[str, str]] = {}
        async for chunk in stream:
            if not chunk.choices:
                continue
            c0 = chunk.choices[0]
            delta = getattr(c0, "delta", None)
            if delta is None:
                continue
            piece = getattr(delta, "content", None) or ""
            if piece:
                content_parts.append(piece)
                yield ("delta", piece)
            tcs = getattr(delta, "tool_calls", None) or []
            for tc in tcs:
                idx = int(getattr(tc, "index", 0) or 0)
                st = tool_calls_state.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""}
                )
                tid = getattr(tc, "id", None)
                if isinstance(tid, str) and tid.strip():
                    st["id"] = tid.strip()
                fn = getattr(tc, "function", None)
                if fn is not None:
                    nm = getattr(fn, "name", None)
                    if isinstance(nm, str) and nm.strip():
                        st["name"] = nm.strip()
                    arg = getattr(fn, "arguments", None)
                    if isinstance(arg, str) and arg:
                        st["arguments"] = st.get("arguments", "") + arg
        resp = MeridianAgent._synthetic_response_from_stream(
            content_parts, tool_calls_state
        )
        yield ("response", resp)

    async def _ensure_tools(self) -> list[dict[str, Any]]:
        if self._openai_tools is not None:
            return self._openai_tools
        mcp_tools = await self._mcp.list_tools()
        mcp_names = {str(t["name"]) for t in mcp_tools if isinstance(t, dict) and "name" in t}
        self._tool_names = set(mcp_names)
        self._openai_tools = _compact_tools_for_llm(mcp_names)
        if not self._openai_tools:
            logger.warning(
                "MCP tools/list had no overlap with Meridian allowlist; server tools=%s",
                sorted(mcp_names),
            )
        return self._openai_tools

    def _build_messages(self, session: SessionState, user_message: str) -> list[dict[str, Any]]:
        cap = self._settings.max_assistant_chars_in_context
        prior: list[dict[str, Any]] = []
        for m in list(session.conversation):
            m2 = dict(m)
            if m2.get("role") == "assistant" and isinstance(m2.get("content"), str):
                c = m2["content"]
                if len(c) > cap:
                    m2["content"] = (
                        c[:cap]
                        + "\n\n… *(Earlier reply shortened so we can keep chatting without errors.)*"
                    )
            prior.append(m2)
        max_items = max(6, self._settings.max_history_turns * 3)
        if len(prior) > max_items:
            prior = prior[-max_items:]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            *prior,
            {"role": "user", "content": user_message},
        ]
        return _trim_messages(messages, self._settings.max_history_turns)

    async def _execute_tool(
        self,
        session: SessionState,
        name: str,
        args: dict[str, Any],
    ) -> tuple[Any, bool]:
        """
        Returns (payload_for_model, auth_gate_fired).
        auth_gate_fired True if we blocked a sensitive tool due to missing auth.
        """
        if name not in self._tool_names:
            return {"error": f"Unknown tool {name!r}"}, False

        if name in SENSITIVE_TOOLS and not session.authenticated_customer_id:
            return {
                "error": "authentication_required",
                "guidance": (
                    "Customer is not verified. Reply warmly: you can help with products now; "
                    "for this request they should verify with Meridian email + 4-digit PIN "
                    "(then you can call verify_customer_pin)."
                ),
            }, True

        if name == VERIFY_TOOL:
            session.reset_auth()
            return await self._mcp.call_tool(name, args), False

        if name in PUBLIC_TOOLS:
            return await self._mcp.call_tool(name, args), False

        args = dict(args or {})

        if name in ("get_customer", "list_orders", "create_order"):
            cid = session.authenticated_customer_id
            if cid:
                args["customer_id"] = cid

        if name == "get_order" and not session.authenticated_customer_id:
            return {"error": "authentication_required"}, True

        return await self._mcp.call_tool(name, args), False

    def _record_verify_outcome(
        self, session: SessionState, tool_result: Any, args: dict[str, Any]
    ) -> None:
        cid = extract_customer_id_from_tool_result(tool_result)
        if not cid:
            return
        session.authenticated_customer_id = cid
        email = args.get("email")
        if isinstance(email, str):
            session.authenticated_email = email.strip().lower() or None

    async def chat(self, session: SessionState, user_message: str) -> dict[str, Any]:
        marker = detect_prompt_injection(user_message)
        if marker:
            return {
                "message": (
                    "I can only help with Meridian product and order questions. "
                    "Please rephrase your request without meta-instructions."
                ),
                "requires_auth": not bool(session.authenticated_customer_id),
                "tool_used": None,
                "confidence": 0.95,
            }

        tools = await self._ensure_tools()
        messages = self._build_messages(session, user_message)

        last_tool: str | None = None
        auth_gate_fired = False
        tool_error = False

        cap = self._settings.max_tool_result_chars
        for _round in range(self._settings.max_tool_rounds):
            _shrink_tool_contents_in_messages(messages, cap)
            response = await self._chat_completion(
                model=self._model_id,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.35,
                max_tokens=1536,
            )
            choice = response.choices[0]
            msg = choice.message

            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                final_text = (msg.content or "").strip() or (
                    "I could not generate a response. Please try again in a moment."
                )
                assistant_entry: dict[str, Any] = {"role": "assistant", "content": final_text}
                messages.append(assistant_entry)
                session.conversation = messages[1:]
                return self._structured(
                    message=final_text,
                    session=session,
                    tool_used=last_tool,
                    auth_gate_fired=auth_gate_fired,
                    tool_error=tool_error,
                )

            assistant_entry = {
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_entry)

            for tc in tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                last_tool = name
                try:
                    payload, gated = await self._execute_tool(session, name, args)
                    if gated:
                        auth_gate_fired = True
                    if isinstance(payload, dict) and payload.get("error"):
                        tool_error = True
                    if name == VERIFY_TOOL:
                        self._record_verify_outcome(session, payload, args)
                except Exception as exc:
                    tool_error = True
                    payload = {"error": str(exc)}
                    logger.exception("Tool %s failed", name)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _tool_message_content(payload, max_chars=cap),
                    }
                )

        final_text = (
            "I reached my internal limit for tool actions on that request. "
            "Please narrow the question or contact Meridian support."
        )
        messages.append({"role": "assistant", "content": final_text})
        session.conversation = messages[1:]
        return self._structured(
            message=final_text,
            session=session,
            tool_used=last_tool,
            auth_gate_fired=auth_gate_fired,
            tool_error=True,
        )

    async def chat_stream(
        self, session: SessionState, user_message: str
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Same behavior as chat(), but emits NDJSON-friendly dicts for streaming UIs.

        Events:
        - {"event": "delta", "text": "..."} — assistant text fragments (final turn only).
        - {"event": "final", "session_id", "message", "requires_auth", "tool_used", "confidence"}
        """
        marker = detect_prompt_injection(user_message)
        if marker:
            out = {
                "message": (
                    "I can only help with Meridian product and order questions. "
                    "Please rephrase your request without meta-instructions."
                ),
                "requires_auth": not bool(session.authenticated_customer_id),
                "tool_used": None,
                "confidence": 0.95,
            }
            yield {
                "event": "final",
                "session_id": session.session_id,
                "message": out["message"],
                "requires_auth": bool(out["requires_auth"]),
                "tool_used": out["tool_used"],
                "confidence": out["confidence"],
            }
            return

        tools = await self._ensure_tools()
        messages = self._build_messages(session, user_message)

        last_tool: str | None = None
        auth_gate_fired = False
        tool_error = False

        cap = self._settings.max_tool_result_chars
        for _round in range(self._settings.max_tool_rounds):
            _shrink_tool_contents_in_messages(messages, cap)
            stream = await self._create_chat_completion_stream(
                model=self._model_id,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.35,
                max_tokens=1536,
            )
            response: Any | None = None
            async for kind, payload in self._iter_stream_deltas(stream):
                if kind == "delta":
                    yield {"event": "delta", "text": payload}
                elif kind == "response":
                    response = payload
            if response is None:
                raise RuntimeError("stream produced no completion")

            choice = response.choices[0]
            msg = choice.message

            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                final_text = (msg.content or "").strip() or (
                    "I could not generate a response. Please try again in a moment."
                )
                assistant_entry: dict[str, Any] = {"role": "assistant", "content": final_text}
                messages.append(assistant_entry)
                session.conversation = messages[1:]
                structured = self._structured(
                    message=final_text,
                    session=session,
                    tool_used=last_tool,
                    auth_gate_fired=auth_gate_fired,
                    tool_error=tool_error,
                )
                yield {
                    "event": "final",
                    "session_id": session.session_id,
                    "message": structured["message"],
                    "requires_auth": structured["requires_auth"],
                    "tool_used": structured["tool_used"],
                    "confidence": structured["confidence"],
                }
                return

            assistant_entry = {
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_entry)

            for tc in tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                last_tool = name
                try:
                    payload, gated = await self._execute_tool(session, name, args)
                    if gated:
                        auth_gate_fired = True
                    if isinstance(payload, dict) and payload.get("error"):
                        tool_error = True
                    if name == VERIFY_TOOL:
                        self._record_verify_outcome(session, payload, args)
                except Exception as exc:
                    tool_error = True
                    payload = {"error": str(exc)}
                    logger.exception("Tool %s failed", name)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _tool_message_content(payload, max_chars=cap),
                    }
                )

        final_text = (
            "I reached my internal limit for tool actions on that request. "
            "Please narrow the question or contact Meridian support."
        )
        messages.append({"role": "assistant", "content": final_text})
        session.conversation = messages[1:]
        structured = self._structured(
            message=final_text,
            session=session,
            tool_used=last_tool,
            auth_gate_fired=auth_gate_fired,
            tool_error=True,
        )
        yield {
            "event": "final",
            "session_id": session.session_id,
            "message": structured["message"],
            "requires_auth": structured["requires_auth"],
            "tool_used": structured["tool_used"],
            "confidence": structured["confidence"],
        }

    @staticmethod
    def _structured(
        *,
        message: str,
        session: SessionState,
        tool_used: str | None,
        auth_gate_fired: bool,
        tool_error: bool,
    ) -> dict[str, Any]:
        requires_auth = not session.authenticated_customer_id and (
            auth_gate_fired or (tool_used == VERIFY_TOOL and tool_error)
        )

        confidence = 0.82
        if tool_error:
            confidence -= 0.15
        if auth_gate_fired:
            confidence -= 0.05
        confidence = max(0.0, min(1.0, confidence))

        return {
            "message": message,
            "requires_auth": bool(requires_auth),
            "tool_used": tool_used,
            "confidence": round(confidence, 2),
        }
