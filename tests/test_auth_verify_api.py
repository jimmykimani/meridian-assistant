"""POST /auth/verify (and aliases) with MCP mocked — no live Meridian server."""

from __future__ import annotations

import types
from typing import Any

import pytest
from fastapi.testclient import TestClient

_TEST_CUSTOMER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-placeholder-key-not-used-for-health")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.1-8b-instant")
    from main import app

    with TestClient(app) as client:
        yield client


def test_auth_verify_success_sets_session(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from main import app

    mcp = app.state.mcp

    async def fake_call_tool(self: Any, name: str, arguments: dict[str, Any]) -> dict[str, str]:
        assert name == "verify_customer_pin"
        assert arguments["email"] == "donaldgarcia@example.net"
        assert arguments["pin"] == "7912"
        return {"customer_id": _TEST_CUSTOMER_UUID}

    monkeypatch.setattr(mcp, "call_tool", types.MethodType(fake_call_tool, mcp))
    sid = api_client.post("/sessions/new").json()["session_id"]
    r = api_client.post(
        "/auth/verify",
        json={"session_id": sid, "email": "donaldgarcia@example.net", "pin": "7912"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["authenticated"] is True
    assert data["session_id"] == sid
    assert "signed in" in (data.get("message") or "").lower()

    st = api_client.get(f"/sessions/{sid}/status").json()
    assert st["authenticated"] is True


def test_auth_verify_wrong_pin_returns_401(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from main import app

    mcp = app.state.mcp

    async def fake_call_tool(self: Any, name: str, arguments: dict[str, Any]) -> None:
        assert name == "verify_customer_pin"
        raise RuntimeError("invalid pin")

    monkeypatch.setattr(mcp, "call_tool", types.MethodType(fake_call_tool, mcp))
    sid = api_client.post("/sessions/new").json()["session_id"]
    r = api_client.post(
        "/auth/verify",
        json={"session_id": sid, "email": "donaldgarcia@example.net", "pin": "0000"},
    )
    assert r.status_code == 401
    detail = r.json().get("detail", "")
    assert "verify" in detail.lower() or "credentials" in detail.lower()

    st = api_client.get(f"/sessions/{sid}/status").json()
    assert st["authenticated"] is False


def test_auth_verify_no_customer_id_returns_401(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from main import app

    mcp = app.state.mcp

    async def fake_call_tool(self: Any, name: str, arguments: dict[str, Any]) -> str:
        return "Verified but no UUID in payload."

    monkeypatch.setattr(mcp, "call_tool", types.MethodType(fake_call_tool, mcp))
    sid = api_client.post("/sessions/new").json()["session_id"]
    r = api_client.post(
        "/auth/verify",
        json={"session_id": sid, "email": "donaldgarcia@example.net", "pin": "7912"},
    )
    assert r.status_code == 401
