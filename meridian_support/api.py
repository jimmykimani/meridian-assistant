"""FastAPI API for Meridian support chatbot (Phase 4)."""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import APIStatusError, AuthenticationError, RateLimitError
from pydantic import BaseModel, Field

from meridian_support.agent import MeridianAgent, extract_customer_id_from_tool_result
from meridian_support.auth_utils import mask_email
from meridian_support.mcp_client import MCPClient
from meridian_support.session_manager import SessionManager
from meridian_support.settings import Settings

logger = logging.getLogger(__name__)


def _upstream_error_message(exc: APIStatusError) -> str:
    """Best-effort parse of Groq/OpenAI error body for logging and user hints."""
    body = getattr(exc, "body", None)
    inner: str | None = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            inner = (err.get("message") or "").strip() or None
            if not inner and isinstance(err.get("code"), (str, int)):
                inner = str(err.get("code")).strip() or None
        elif isinstance(err, str):
            inner = err.strip() or None
    if not inner:
        m = getattr(exc, "message", None)
        if m:
            inner = str(m).strip() or None
    if inner and len(inner) > 600:
        inner = inner[:600] + "…"
    return inner or ""


def _http_exception_from_groq(exc: APIStatusError) -> HTTPException:
    """Turn OpenAI-compatible API errors into clear HTTPException.detail for /chat."""
    code = exc.status_code or 0
    upstream = _upstream_error_message(exc)
    logger.warning("Groq API error status=%s upstream=%r body=%s", code, upstream, getattr(exc, "body", None))

    if code == 429:
        return HTTPException(
            status_code=429,
            detail=(
                "Too many requests to the AI right now. Wait a bit and try again, "
                "or check your Groq limits at https://console.groq.com/"
            ),
        )
    if code == 413:
        return HTTPException(
            status_code=502,
            detail=(
                "That message or conversation is too large for the AI. "
                "Try a shorter question, fewer products at once, or clear chat history."
            ),
        )
    if code == 400:
        hint = "Try rephrasing in plain English, or ask for a smaller slice of the catalog."
        low = upstream.lower()
        if "context" in low and ("length" in low or "window" in low or "token" in low):
            return HTTPException(
                status_code=502,
                detail=(
                    "That request is too long for the AI context window. "
                    "Shorten your message or clear older chat and try again."
                ),
            )
        if "tool" in low and ("invalid" in low or "parse" in low or "json" in low or "schema" in low):
            return HTTPException(
                status_code=502,
                detail=(
                    "The assistant hit a problem using a catalog tool. "
                    "Please try again, or ask for a more specific product question."
                ),
            )
        if upstream:
            return HTTPException(
                status_code=502,
                detail=f"We couldn’t run the assistant on that input. {upstream} {hint}".strip(),
            )
        return HTTPException(
            status_code=502,
            detail=f"We couldn’t run the assistant on that input. {hint}",
        )
    if code in (401, 403):
        return HTTPException(
            status_code=502,
            detail="The AI service rejected the API key or permissions. Check GROQ_API_KEY and your Groq project.",
        )
    if code == 404:
        return HTTPException(
            status_code=502,
            detail="The configured AI model was not found. Check GROQ_MODEL is a valid Groq model id.",
        )
    if code >= 500:
        return HTTPException(
            status_code=502,
            detail="The AI service had a temporary problem. Try again in a moment.",
        )
    if upstream:
        return HTTPException(
            status_code=502,
            detail=f"The AI returned an error ({code}): {upstream}",
        )
    return HTTPException(
        status_code=502,
        detail=f"The AI returned an error ({code}). Please try again.",
    )


def _cors_origins() -> list[str]:
    raw = os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:8501,http://127.0.0.1:8501",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    mcp = MCPClient(url=settings.mcp_server_url)
    agent = MeridianAgent(settings, mcp)
    sessions = SessionManager()
    app.state.settings = settings
    app.state.mcp = mcp
    app.state.agent = agent
    app.state.sessions = sessions
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    logger.info("Meridian API startup complete (model=%s)", settings.groq_model)
    yield
    await agent.aclose()
    await mcp.aclose()
    logger.info("Meridian API shutdown")


app = FastAPI(
    title="Meridian Support API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(..., min_length=1, max_length=8000)


class ChatResponse(BaseModel):
    session_id: str
    message: str
    requires_auth: bool
    tool_used: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class NewSessionResponse(BaseModel):
    session_id: str


class SessionStatusResponse(BaseModel):
    session_id: str
    authenticated: bool
    email_masked: str | None = None


class AuthVerifyRequest(BaseModel):
    session_id: str | None = None
    email: str = Field(..., min_length=3, max_length=320)
    pin: str = Field(..., min_length=4, max_length=32)


class AuthVerifyResponse(BaseModel):
    session_id: str
    authenticated: bool
    message: str


def _get_or_create_session(request: Request, session_id: str | None) -> Any:
    """Reuse existing session or create a new one (anonymous browsing allowed)."""
    sm: SessionManager = request.app.state.sessions
    if not session_id:
        return sm.new_session()
    state = sm.get(session_id)
    if state is None:
        # Do not silently mint a new session: client would lose auth bound to the old id
        # (common with multi-worker / cold restarts). Force an explicit refresh.
        raise HTTPException(
            status_code=410,
            detail=(
                "This chat session is no longer on the server (restart, scaling, or expiry). "
                "Use Clear chat history or reload, then sign in again if needed."
            ),
        )
    return state


@app.get("/")
async def root(request: Request) -> dict[str, str]:
    """So you can curl http://127.0.0.1:8000/ and confirm this is the Meridian API."""
    settings: Settings = request.app.state.settings
    return {
        "service": "meridian-support-api",
        "llm_model": settings.groq_model,
        "health": "/health",
        "sign_in": "/auth/verify (aliases: /login, /verify)",
        "docs": "/docs",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions/new", response_model=NewSessionResponse)
async def new_session(request: Request) -> NewSessionResponse:
    sm: SessionManager = request.app.state.sessions
    s = sm.new_session()
    return NewSessionResponse(session_id=s.session_id)


class ResetSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1)


@app.post("/sessions/reset", response_model=NewSessionResponse)
async def reset_session(request: Request, body: ResetSessionRequest) -> NewSessionResponse:
    """Clear auth + conversation but keep the same session id."""
    sm: SessionManager = request.app.state.sessions
    try:
        s = sm.require(body.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session_id") from None
    s.clear_conversation()
    s.reset_auth()
    return NewSessionResponse(session_id=s.session_id)


@app.post("/sessions/clear-chat", response_model=NewSessionResponse)
async def clear_chat_only(request: Request, body: ResetSessionRequest) -> NewSessionResponse:
    """Clear chat transcript only; keep authentication."""
    sm: SessionManager = request.app.state.sessions
    try:
        s = sm.require(body.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session_id") from None
    s.clear_conversation()
    return NewSessionResponse(session_id=s.session_id)


@app.get("/sessions/{session_id}/status", response_model=SessionStatusResponse)
async def session_status(session_id: str, request: Request) -> SessionStatusResponse:
    sm: SessionManager = request.app.state.sessions
    try:
        s = sm.require(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session_id") from None
    return SessionStatusResponse(
        session_id=s.session_id,
        authenticated=bool(s.authenticated_customer_id),
        email_masked=mask_email(s.authenticated_email),
    )


@app.post("/auth/verify", response_model=AuthVerifyResponse)
@app.post("/login", response_model=AuthVerifyResponse)
@app.post("/verify", response_model=AuthVerifyResponse)
async def auth_verify(request: Request, body: AuthVerifyRequest) -> AuthVerifyResponse:
    """Verify email + PIN via MCP; binds customer to session."""
    mcp: MCPClient = request.app.state.mcp
    sm: SessionManager = request.app.state.sessions
    if body.session_id:
        try:
            session = sm.require(body.session_id)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Unknown session_id. Use Clear chat history, then verify again "
                    "(sessions are lost when the API restarts)."
                ),
            ) from None
    else:
        session = sm.new_session()

    session.reset_auth()
    try:
        result = await mcp.call_tool(
            "verify_customer_pin",
            {"email": body.email.strip(), "pin": body.pin.strip()},
        )
    except RuntimeError as exc:
        logger.info("verify_customer_pin failed: %s", exc)
        raise HTTPException(
            status_code=401,
            detail="We could not verify those credentials. Check your email and PIN.",
        ) from exc

    cid = extract_customer_id_from_tool_result(result)
    if not cid:
        raise HTTPException(
            status_code=401,
            detail="We could not verify those credentials. Check your email and PIN.",
        )

    session.authenticated_customer_id = cid
    session.authenticated_email = body.email.strip().lower()
    # Drop pre-login chat turns so the model does not keep asking for PIN after a successful verify.
    session.clear_conversation()
    session.conversation.append(
        {
            "role": "assistant",
            "content": (
                "Meridian account sign-in succeeded for this session. "
                "Help with orders, checkout, and account details without asking for email or PIN again "
                "unless the customer signs out."
            ),
        }
    )
    return AuthVerifyResponse(
        session_id=session.session_id,
        authenticated=True,
        message="You're signed in. You can place an order or ask about your purchases now.",
    )


@app.post("/auth/logout", response_model=SessionStatusResponse)
async def auth_logout(request: Request, body: ResetSessionRequest) -> SessionStatusResponse:
    """Sign out: clear auth and chat history for this session."""
    sm: SessionManager = request.app.state.sessions
    try:
        s = sm.require(body.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session_id") from None
    s.reset_auth()
    s.clear_conversation()
    return SessionStatusResponse(
        session_id=s.session_id,
        authenticated=False,
        email_masked=None,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    agent: MeridianAgent = request.app.state.agent
    session = _get_or_create_session(request, body.session_id)
    t0 = time.perf_counter()
    try:
        out = await agent.chat(session, body.message.strip())
    except RuntimeError as exc:
        logger.warning("chat failed: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RateLimitError as exc:
        logger.warning("Groq rate limit: %s", exc)
        raise HTTPException(
            status_code=429,
            detail=(
                "Groq rate limit or quota hit. Wait and retry, or check your plan at "
                "https://console.groq.com/"
            ),
        ) from exc
    except AuthenticationError as exc:
        logger.warning("Groq auth failed: %s", exc)
        raise HTTPException(
            status_code=403,
            detail="Groq rejected the request (check GROQ_API_KEY).",
        ) from exc
    except APIStatusError as exc:
        raise _http_exception_from_groq(exc) from exc
    except Exception:
        logger.exception("chat failed")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again.",
        ) from None

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(
        "chat ok session_id=%s ms=%.0f tool_used=%s requires_auth=%s",
        session.session_id,
        elapsed_ms,
        out.get("tool_used"),
        bool(out.get("requires_auth")),
    )

    return ChatResponse(
        session_id=session.session_id,
        message=out["message"],
        requires_auth=bool(out.get("requires_auth")),
        tool_used=out.get("tool_used"),
        confidence=float(out.get("confidence", 0.75)),
    )
