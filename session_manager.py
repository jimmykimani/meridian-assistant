"""In-memory session store for chat + authentication state."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionState:
    """One browser or API client session."""

    session_id: str
    authenticated_customer_id: str | None = None
    authenticated_email: str | None = None
    # OpenAI-style chat messages (no system) for multi-turn + tool calls.
    conversation: list[dict[str, Any]] = field(default_factory=list)

    def clear_conversation(self) -> None:
        self.conversation.clear()

    def reset_auth(self) -> None:
        self.authenticated_customer_id = None
        self.authenticated_email = None


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def new_session(self) -> SessionState:
        sid = secrets.token_urlsafe(16)
        state = SessionState(session_id=sid)
        self._sessions[sid] = state
        return state

    def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def require(self, session_id: str) -> SessionState:
        state = self.get(session_id)
        if state is None:
            raise KeyError("Unknown session_id")
        return state
