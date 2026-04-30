"""Optional LangSmith tracing for OpenAI-compatible clients (Groq)."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _langsmith_tracing_enabled() -> bool:
    v = (
        os.environ.get("LANGSMITH_TRACING")
        or os.environ.get("LANGCHAIN_TRACING_V2")
        or ""
    ).strip().lower()
    return v in ("1", "true", "yes")


def _langsmith_api_key_present() -> bool:
    return bool(
        (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY") or "").strip()
    )


def wrap_openai_client_for_tracing(client: Any) -> Any:
    """Patch AsyncOpenAI so chat.completions calls appear in LangSmith when env is set."""
    if not _langsmith_tracing_enabled():
        return client
    if not _langsmith_api_key_present():
        logger.warning(
            "LangSmith tracing is enabled but LANGSMITH_API_KEY (or LANGCHAIN_API_KEY) is missing"
        )
        return client
    try:
        from langsmith.wrappers import wrap_openai

        return wrap_openai(client, chat_name="meridian-support-groq")
    except ImportError:
        logger.warning("langsmith package not installed; tracing disabled")
        return client
