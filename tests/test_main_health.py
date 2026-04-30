from __future__ import annotations

import json
import types
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-placeholder-key-not-used-for-health")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.1-8b-instant")
    from main import app

    with TestClient(app) as client:
        yield client


def test_health(api_client: TestClient) -> None:
    r = api_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_identifies_service(api_client: TestClient) -> None:
    r = api_client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body.get("service") == "meridian-support-api"
    assert body.get("llm_model") == "llama-3.1-8b-instant"


def test_new_session(api_client: TestClient) -> None:
    r = api_client.post("/sessions/new")
    assert r.status_code == 200
    body = r.json()
    assert "session_id" in body
    assert len(body["session_id"]) > 8


def test_reset_session(api_client: TestClient) -> None:
    sid = api_client.post("/sessions/new").json()["session_id"]
    r = api_client.post("/sessions/reset", json={"session_id": sid})
    assert r.status_code == 200
    assert r.json()["session_id"] == sid


def test_session_status(api_client: TestClient) -> None:
    sid = api_client.post("/sessions/new").json()["session_id"]
    r = api_client.get(f"/sessions/{sid}/status")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["authenticated"] is False


def test_session_status_unknown(api_client: TestClient) -> None:
    r = api_client.get("/sessions/definitely-unknown-id/status")
    assert r.status_code == 404


def test_chat_unknown_session_returns_410(api_client: TestClient) -> None:
    r = api_client.post(
        "/chat",
        json={"session_id": "definitely-unknown-session-id", "message": "hello"},
    )
    assert r.status_code == 410
    assert "session" in r.json().get("detail", "").lower()


def test_chat_stream_unknown_session_returns_410(api_client: TestClient) -> None:
    with api_client.stream(
        "POST",
        "/chat/stream",
        json={"session_id": "definitely-unknown-session-id", "message": "hello"},
    ) as r:
        assert r.status_code == 410


def test_chat_full_catalog_bypass(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from main import app

    mcp = app.state.mcp

    async def fake_call_tool(self: Any, name: str, arguments: dict[str, Any]) -> str:
        assert name == "list_products"
        assert arguments.get("is_active") is True
        return "1. [X-1] Demo — $1 | 1 unit"

    monkeypatch.setattr(mcp, "call_tool", types.MethodType(fake_call_tool, mcp))
    sid = api_client.post("/sessions/new").json()["session_id"]
    r = api_client.post("/chat", json={"session_id": sid, "message": "show ALL products !!!!"})
    assert r.status_code == 200
    data = r.json()
    assert "Active products" in data["message"]
    assert "[X-1]" in data["message"]
    assert data.get("tool_used") == "list_products"


def test_chat_stream_ndjson_mocked(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from main import app

    agent = app.state.agent

    async def fake_chat_stream(self, session, user_message: str):
        yield {"event": "delta", "text": "Hi "}
        yield {"event": "delta", "text": "there"}
        yield {
            "event": "final",
            "session_id": session.session_id,
            "message": "Hi there",
            "requires_auth": False,
            "tool_used": None,
            "confidence": 0.9,
        }

    monkeypatch.setattr(agent, "chat_stream", types.MethodType(fake_chat_stream, agent))
    sid = api_client.post("/sessions/new").json()["session_id"]
    with api_client.stream(
        "POST",
        "/chat/stream",
        json={"session_id": sid, "message": "hello"},
    ) as r:
        assert r.status_code == 200
        lines = [ln for ln in r.iter_lines() if ln]
    events = [json.loads(ln) for ln in lines]
    assert [e.get("event") for e in events] == ["delta", "delta", "final"]
    assert events[-1]["message"] == "Hi there"


def test_logout_clears_auth(api_client: TestClient) -> None:
    sid = api_client.post("/sessions/new").json()["session_id"]
    r = api_client.post("/auth/logout", json={"session_id": sid})
    assert r.status_code == 200
    st = api_client.get(f"/sessions/{sid}/status").json()
    assert st["authenticated"] is False
