"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(..., min_length=1, max_length=8000)


class ChatResponse(BaseModel):
    session_id: str
    message: str
    requires_auth: bool
    tool_used: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class NewSessionResponse(BaseModel):
    session_id: str


class SessionStatusResponse(BaseModel):
    session_id: str
    authenticated: bool
    email_masked: str | None = None


class AuthVerifyRequest(BaseModel):
    session_id: str | None = None
    email: str = Field(..., min_length=3, max_length=320)
    pin: str = Field(..., min_length=4, max_length=32)


class AuthVerifyResponse(BaseModel):
    session_id: str
    authenticated: bool
    message: str


class ResetSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
