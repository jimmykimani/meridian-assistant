"""
Meridian Support — Streamlit UI.

UX: anonymous chat for catalog / general help; verify in sidebar only when
customers need orders, account details, or purchases.
"""

from __future__ import annotations

import html
import json
import os
import re
from typing import Any

import httpx
import streamlit as st
import streamlit.components.v1 as components

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
_SHOW_API_URL = _env_or_secret("MERIDIAN_SHOW_API_URL", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# SKUs as shown by the assistant, e.g. [COM-0012] …
_SKU_IN_BRACKETS = re.compile(r"\[([A-Z][A-Z0-9]{1,11}-\d+)\]")


def _extract_skus_from_message(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _SKU_IN_BRACKETS.finditer(text):
        sku = m.group(1)
        if sku not in seen:
            seen.add(sku)
            out.append(sku)
    return out


def _assistant_copy_buttons(content: str, *, component_key: str) -> None:
    """Per-assistant-message SKU copy chips (isolated iframe)."""
    skus = _extract_skus_from_message(content)[:16]
    if not skus:
        return
    chips = "".join(
        f'<button type="button" class="mer-sku" data-sku="{html.escape(sku, quote=True)}">'
        f"{html.escape(sku)}</button>"
        for sku in skus
    )
    sku_row = f'<div class="mer-row"><span class="mer-hint">Copy SKU</span>{chips}</div>'
    html_block = f"""
<div class="mer-tools" data-mer="{html.escape(component_key[:80], quote=True)}">
  {sku_row}
</div>
<script>
(function() {{
  document.querySelectorAll(".mer-sku").forEach(function(b) {{
    b.addEventListener("click", function() {{
      var sku = b.getAttribute("data-sku") || b.textContent;
      navigator.clipboard.writeText(sku).then(function() {{
        var o = b.textContent; b.textContent = "Copied"; setTimeout(function() {{ b.textContent = o; }}, 1200);
      }}).catch(function() {{}});
    }});
  }});
}})();
</script>
<style>
.mer-tools {{ font-family: 'Plus Jakarta Sans', system-ui, sans-serif; margin-top: 0.25rem; }}
.mer-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem; margin-top: 0.3rem; }}
.mer-hint {{ font-size: 0.7rem; color: #94a3b8; margin-right: 0.1rem; }}
.mer-sku {{
  font-size: 0.72rem; padding: 0.28rem 0.6rem; border-radius: 8px;
  border: 1px solid rgba(148,163,184,0.45); background: rgba(51,65,85,0.9);
  color: #f8fafc; cursor: pointer;
}}
</style>
"""
    h = max(44, 28 + (len(skus) // 4) * 28)
    components.html(html_block, height=h, scrolling=False)


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


def _decode_json_chat(r: httpx.Response) -> tuple[str, dict[str, Any], str | None]:
    """
    Parse ``POST /chat`` JSON. Returns (assistant_text, meta, session_id_or_none).
    Does not touch ``st.session_state`` (caller applies session_id).
    """
    meta: dict[str, Any] = {}
    if r.status_code == 410:
        try:
            err_body = r.json().get("detail", r.text)
        except Exception:
            err_body = r.text
        meta = {"error": err_body, "status_code": 410}
        return (
            f"{err_body}\n\nA new session was started—sign in again from the sidebar if you need your account.",
            meta,
            None,
        )
    if r.status_code >= 400:
        try:
            err_body = r.json().get("detail", r.text)
        except Exception:
            err_body = r.text
        meta = {"error": err_body, "status_code": r.status_code}
        return str(err_body), meta, None
    data = r.json()
    assistant_text = data.get("message", "") or ""
    meta = {
        "requires_auth": data.get("requires_auth"),
        "tool_used": data.get("tool_used"),
        "confidence": data.get("confidence"),
    }
    sid = data.get("session_id")
    return assistant_text, meta, str(sid).strip() if sid else None


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
                        data = r.json()
                        st.session_state.session_id = data.get("session_id") or st.session_state.session_id
                        _sync_server_auth()
                        # Match server-side cleared transcript so the model is not stuck on old "verify first" turns.
                        st.session_state.messages = []
                        welcome = (data.get("message") or "").strip() or (
                            "You're signed in. You can place an order or ask about your purchases now."
                        )
                        st.session_state.messages.append(
                            {"role": "assistant", "content": welcome, "meta": {}}
                        )
                        st.success("Signed in successfully — chat was refreshed for your account.")
                        st.rerun()


def _render_chat() -> None:
    st.markdown("### Meridian Support")
    st.caption(
        "Ask about monitors, keyboards, stock, or shipping. "
        "Sign in from the sidebar **only** when you need account or order actions."
    )

    for idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("role") == "assistant" and (msg.get("content") or "").strip():
                _assistant_copy_buttons(
                    str(msg["content"]),
                    component_key=f"mer_copy_{idx}_{len(st.session_state.messages)}",
                )

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
        with st.chat_message("assistant"):
            slot = st.empty()
            slot.caption("Thinking…")
            try:
                with httpx.Client(timeout=CHAT_TIMEOUT) as client:
                    with client.stream("POST", f"{API_BASE}/chat/stream", json=payload) as r:
                        if r.status_code == 404:
                            fb = _post("/chat", payload)
                            assistant_text, meta, sid = _decode_json_chat(fb)
                            if sid:
                                st.session_state.session_id = sid
                            if meta.get("status_code") == 410:
                                st.session_state.session_id = None
                                st.session_state.messages = []
                                _ensure_session_id()
                            slot.markdown(assistant_text)
                        elif r.status_code == 410:
                            raw = r.read()
                            try:
                                err_body = json.loads(raw).get("detail", raw.decode("utf-8", errors="replace"))
                            except Exception:
                                err_body = raw.decode("utf-8", errors="replace")
                            st.session_state.session_id = None
                            st.session_state.messages = []
                            _ensure_session_id()
                            meta = {"error": err_body, "status_code": 410}
                            assistant_text = (
                                f"{err_body}\n\nA new session was started—sign in again from the sidebar if you need your account."
                            )
                            slot.markdown(assistant_text)
                        elif r.status_code >= 400:
                            raw = r.read()
                            try:
                                err_body = json.loads(raw).get("detail", raw.decode("utf-8", errors="replace"))
                            except Exception:
                                err_body = raw.decode("utf-8", errors="replace")
                            meta = {"error": err_body, "status_code": r.status_code}
                            assistant_text = str(err_body)
                            slot.markdown(assistant_text)
                        else:
                            saw_final = False
                            buf = ""
                            for line in r.iter_lines():
                                if not line or not str(line).strip():
                                    continue
                                try:
                                    ev = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                ev_type = ev.get("event")
                                if ev_type == "delta":
                                    buf += str(ev.get("text", ""))
                                    slot.markdown(buf + "▌")
                                elif ev_type == "final":
                                    saw_final = True
                                    assistant_text = str(ev.get("message", buf))
                                    meta = {
                                        "requires_auth": ev.get("requires_auth"),
                                        "tool_used": ev.get("tool_used"),
                                        "confidence": ev.get("confidence"),
                                    }
                                    sid = ev.get("session_id")
                                    if sid:
                                        st.session_state.session_id = sid
                                    slot.markdown(assistant_text)
                                elif ev_type == "error":
                                    meta = {
                                        "error": ev.get("detail", "stream error"),
                                        "status_code": ev.get("status_code", 502),
                                    }
                                    assistant_text = str(ev.get("detail", buf))
                                    slot.markdown(assistant_text)
                                    saw_final = True
                            if not saw_final and not meta.get("error"):
                                meta = {"error": "Stream ended without a final event", "status_code": 502}
                                assistant_text = buf or "No response from the assistant."
                                slot.markdown(assistant_text)
            except Exception as exc:
                meta = {"error": str(exc)}
                assistant_text = str(exc)
                slot.markdown(assistant_text)

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
        if _SHOW_API_URL:
            with st.expander("Developer — API base URL"):
                st.code(API_BASE, language="text")
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
