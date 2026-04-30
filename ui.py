"""
Meridian Support — Streamlit UI.

UX: anonymous chat for catalog / general help; verify in sidebar only when
customers need orders, account details, or purchases.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

DEFAULT_API = "http://127.0.0.1:8000"


def _env_or_secret(key: str, default: str) -> str:
    """Prefer shell/Docker env; use Streamlit Community Cloud secrets when unset."""
    v = (os.environ.get(key) or "").strip()
    if v:
        return v
    try:
        out = st.secrets[key]
    except (FileNotFoundError, KeyError, TypeError):
        return default
    if out is None:
        return default
    s = str(out).strip()
    return s if s else default


API_BASE = _env_or_secret("MERIDIAN_API_URL", DEFAULT_API).rstrip("/")
CHAT_TIMEOUT = float(_env_or_secret("MERIDIAN_CHAT_TIMEOUT", "180"))


def _inject_theme() -> None:
    st.markdown(
        """
        <style>
          @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');
          html, body, [class*="css"]  { font-family: 'Plus Jakarta Sans', system-ui, sans-serif; }
          .stApp {
            background: radial-gradient(1200px 800px at 10% -10%, rgba(56,189,248,0.12), transparent 55%),
                        radial-gradient(900px 600px at 100% 0%, rgba(99,102,241,0.14), transparent 45%),
                        linear-gradient(165deg, #070b14 0%, #0c1222 40%, #0f172a 100%);
          }
          [data-testid="stHeader"] { background: rgba(15,23,42,0.85); backdrop-filter: blur(12px); border-bottom: 1px solid rgba(148,163,184,0.12); }
          [data-testid="stToolbar"] { display: none; }
          section[data-testid="stSidebar"] {
            background: rgba(15,23,42,0.72);
            border-right: 1px solid rgba(148,163,184,0.12);
          }
          section[data-testid="stSidebar"] .block-container { padding-top: 1.5rem; }
          div[data-testid="stVerticalBlock"] > div:has(> label) label { color: #cbd5e1 !important; font-weight: 500 !important; }
          [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {
            background: rgba(255,255,255,0.06) !important;
            color: #f8fafc !important;
            border: 1px solid rgba(148,163,184,0.28) !important;
            border-radius: 12px !important;
          }
          [data-testid="stChatInput"] textarea {
            background: rgba(255,255,255,0.06) !important;
            color: #f8fafc !important;
            border-radius: 14px !important;
          }
          [data-testid="stChatMessage"] {
            background: rgba(30,41,59,0.45) !important;
            border: 1px solid rgba(148,163,184,0.12) !important;
            border-radius: 16px !important;
          }
          div.stButton > button[kind="primary"], div.stButton > button:first-child {
            border-radius: 12px;
            font-weight: 600;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _get(path: str) -> httpx.Response:
    with httpx.Client(timeout=30.0) as client:
        return client.get(f"{API_BASE}{path}")


def _post(path: str, payload: dict[str, Any]) -> httpx.Response:
    with httpx.Client(timeout=CHAT_TIMEOUT) as client:
        return client.post(f"{API_BASE}{path}", json=payload)


_VERIFY_PATHS = ("/auth/verify", "/login", "/verify")


def _post_auth_verify(payload: dict[str, Any]) -> httpx.Response:
    last: httpx.Response | None = None
    for path in _VERIFY_PATHS:
        last = _post(path, payload)
        if last.status_code != 404:
            return last
    assert last is not None
    return last


def _api_service_name() -> str | None:
    try:
        r = _get("/")
        if r.status_code != 200:
            return None
        return str(r.json().get("service") or "")
    except Exception:
        return None


def _init_state() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = None
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "signed_in_email" not in st.session_state:
        st.session_state.signed_in_email = None


def _ensure_session_id() -> None:
    if st.session_state.session_id:
        return
    try:
        r = _post("/sessions/new", {})
        if r.status_code == 200:
            st.session_state.session_id = r.json().get("session_id")
    except Exception:
        pass


def _sync_server_auth() -> bool:
    sid = st.session_state.session_id
    if not sid:
        st.session_state.signed_in_email = None
        return False
    r = _get(f"/sessions/{sid}/status")
    if r.status_code == 404:
        st.session_state.session_id = None
        st.session_state.messages = []
        st.session_state.signed_in_email = None
        return False
    try:
        data = r.json()
    except Exception:
        return False
    ok = bool(data.get("authenticated"))
    if ok:
        st.session_state.signed_in_email = data.get("email_masked")
    else:
        st.session_state.signed_in_email = None
    return ok


def _sidebar_account_panel() -> None:
    authed = _sync_server_auth()
    with st.expander("Account — orders & purchases", expanded=not authed):
        st.caption(
            "Browse and ask product questions **without** signing in. "
            "Verify only when you need **order history**, **order details**, "
            "**account info**, or **placing an order**."
        )
        if authed:
            st.success(f"Verified as **{st.session_state.signed_in_email or 'customer'}**")
        with st.form("verify_form"):
            em = st.text_input("Meridian email", placeholder="donaldgarcia@example.net")
            pin = st.text_input("4-digit PIN", type="password", max_chars=12)
            go = st.form_submit_button("Verify account")
        if go:
            if not em or not pin or len(pin.strip()) < 4:
                st.error("Enter email and PIN.")
            else:
                payload = {
                    "email": em.strip(),
                    "pin": pin.strip(),
                    "session_id": st.session_state.session_id,
                }
                with st.spinner("Checking with Meridian…"):
                    try:
                        r = _post_auth_verify(payload)
                    except Exception as exc:
                        st.error(str(exc))
                        r = None
                if r is not None:
                    if r.status_code >= 400:
                        try:
                            detail = r.json().get("detail", r.text)
                        except Exception:
                            detail = r.text
                        extra = ""
                        if r.status_code == 404:
                            extra = (
                                f" Wrong app on port 8000. Open `{API_BASE}/` — "
                                "must show `meridian-support-api`. Restart uvicorn from repo root."
                            )
                        st.error(f"[HTTP {r.status_code}] {detail}{extra}")
                    else:
                        st.session_state.session_id = r.json().get("session_id")
                        _sync_server_auth()
                        st.success("Verified. You can ask about your orders now.")
                        st.rerun()


def _render_chat() -> None:
    st.markdown("### Meridian Support")
    st.caption(
        "Ask about monitors, keyboards, stock, or shipping. "
        "Sign in from the sidebar **only** when you need account or order actions."
    )

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            meta = msg.get("meta") or {}
            if msg["role"] == "assistant" and meta:
                with st.expander("Details"):
                    st.json(meta)

    last = st.session_state.messages[-1] if st.session_state.messages else None
    if (
        last
        and last.get("role") == "assistant"
        and (last.get("meta") or {}).get("requires_auth")
    ):
        st.info(
            "**Next step:** open **Account — orders & purchases** in the sidebar and verify "
            "with your Meridian email + PIN so we can load orders or place purchases safely."
        )

    if prompt := st.chat_input("Try: “Show wireless keyboards under $80” or “Any monitors in stock?”"):
        _ensure_session_id()
        st.session_state.messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {"message": prompt}
        if st.session_state.session_id:
            payload["session_id"] = st.session_state.session_id

        assistant_text = ""
        meta: dict[str, Any] = {}
        try:
            with st.spinner("Thinking… (first MCP call can take ~30–60s)"):
                r = _post("/chat", payload)
            if r.status_code >= 400:
                try:
                    err_body = r.json().get("detail", r.text)
                except Exception:
                    err_body = r.text
                meta = {"error": err_body, "status_code": r.status_code}
                assistant_text = str(err_body)
            else:
                data = r.json()
                st.session_state.session_id = data.get("session_id") or st.session_state.session_id
                assistant_text = data.get("message", "") or ""
                meta = {
                    "requires_auth": data.get("requires_auth"),
                    "tool_used": data.get("tool_used"),
                    "confidence": data.get("confidence"),
                }
        except Exception as exc:
            meta = {"error": str(exc)}
            assistant_text = str(exc)

        st.session_state.messages.append(
            {"role": "assistant", "content": assistant_text, "meta": meta}
        )
        st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="Meridian Support",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_theme()
    _init_state()
    _ensure_session_id()

    with st.sidebar:
        st.markdown("### Meridian Support")
        st.caption("Meridian Electronics")
        st.divider()
        st.markdown(f"**API** `{API_BASE}`")
        ok = False
        try:
            hr = _get("/health")
            ok = hr.status_code == 200 and hr.json().get("status") == "ok"
            st.markdown("**Status:** " + ("Online" if ok else "Down"))
        except Exception:
            st.markdown("**Status:** Unreachable")
        svc = _api_service_name()
        if svc == "meridian-support-api":
            st.caption("Connected to Meridian API")
        elif svc:
            st.warning(f"Port 8000 is `{svc}` — start Meridian Support’s API from the repo root.")
        elif ok:
            st.warning("Unexpected API root — restart uvicorn from the Meridian Support repo root.")

        _sidebar_account_panel()

        st.divider()
        if st.button("Clear chat history", use_container_width=True):
            st.session_state.messages = []
            sid = st.session_state.session_id
            if sid:
                try:
                    _post("/sessions/clear-chat", {"session_id": sid})
                except Exception:
                    pass
            st.rerun()

        if st.session_state.session_id and _sync_server_auth():
            if st.button("Sign out (account only)", use_container_width=True):
                try:
                    r = _post("/auth/logout", {"session_id": st.session_state.session_id})
                    if r.status_code < 400:
                        _sync_server_auth()
                        st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        st.caption("Sessions live in API memory until restart.")

    _render_chat()


main()
