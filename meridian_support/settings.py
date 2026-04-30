"""Central environment-driven settings (no secrets in code)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load repo-root `.env` even when the process cwd is elsewhere.
_REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    groq_model: str
    mcp_server_url: str | None
    max_tool_rounds: int
    max_history_turns: int
    groq_completion_retries: int
    groq_retry_base_sec: float
    max_tool_result_chars: int

    @staticmethod
    def from_env() -> Settings:
        key = (os.environ.get("GROQ_API_KEY") or "").strip()
        if not key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Export it in your shell or use a .env file "
                "(see .env.example). Never commit API keys."
            )
        return Settings(
            groq_api_key=key,
            groq_model=os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant").strip(),
            mcp_server_url=(os.environ.get("MCP_SERVER_URL") or "").strip() or None,
            max_tool_rounds=int(os.environ.get("MAX_TOOL_ROUNDS", "6")),
            max_history_turns=int(os.environ.get("MAX_HISTORY_TURNS", "12")),
            groq_completion_retries=max(1, int(os.environ.get("GROQ_COMPLETION_RETRIES", "6"))),
            groq_retry_base_sec=float(os.environ.get("GROQ_RETRY_BASE_SEC", "1.5")),
            max_tool_result_chars=max(
                512,
                int(os.environ.get("MAX_TOOL_RESULT_CHARS", "5000")),
            ),
        )
