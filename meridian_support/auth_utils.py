"""Shared helpers for Meridian auth API."""

from __future__ import annotations


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}•••{local[-1]}@{domain}"
