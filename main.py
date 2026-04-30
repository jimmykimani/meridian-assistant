"""Uvicorn entrypoint: `uvicorn main:app` (Docker, Render, local)."""

from meridian_support.api import app

__all__ = ["app"]
