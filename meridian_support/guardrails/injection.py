"""Prompt-injection markers for support chat."""

from __future__ import annotations

INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "system prompt",
    "you are now",
    "developer message",
    "override instructions",
)


def detect_prompt_injection(user_text: str) -> str | None:
    low = user_text.lower()
    for marker in INJECTION_MARKERS:
        if marker in low:
            return marker
    return None
