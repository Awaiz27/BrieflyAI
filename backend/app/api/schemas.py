"""Pydantic schemas for API request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, conint


# ── Auth ────────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    user_id: str
    token: str


# ── Threads ─────────────────────────────────────────────────────────────────


class ThreadCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    paper_id: Optional[str] = None


class ThreadResponse(BaseModel):
    chat_id: str
    title: Optional[str]
    focused_paper_ids: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ThreadScopeUpdateRequest(BaseModel):
    paper_ids: List[str] = Field(default_factory=list)


# ── Messages ────────────────────────────────────────────────────────────────


class MessageResponse(BaseModel):
    msg_id: int
    role: str
    content: str
    created_at: datetime


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)
    idempotency_key: Optional[str] = None
    paper_ids: Optional[List[str]] = None
    thinking_mode: Literal["fast", "detailed"] = "detailed"
    llm_provider: Optional[Literal["ollama", "groq"]] = None


# ── Papers / Ranking ────────────────────────────────────────────────────────


class RankRequest(BaseModel):
    window_days: conint(gt=0) = Field(default=1)
    category: Optional[List[str]] = None
    query: Optional[str] = Field(default=None, min_length=1)
    top_k: conint(gt=0, le=100) = Field(default=50)


class Paper(BaseModel):
    paper_id: str
    title: str
    summary: str
    categories: str
    submitted_at: datetime
    score: float = Field(..., ge=0)


class RankResponse(BaseModel):
    results: List[Paper]


class IndexedPaper(BaseModel):
    paper_id: str
    title: str
    summary: Optional[str]
    authors: Optional[str]
    categories: Optional[str]
    category_name: Optional[str] = None
    submitted_at: Optional[datetime]
    link: Optional[str]
    pdf_url: Optional[str]


class IndexedPaperSearchResponse(BaseModel):
    results: List[IndexedPaper]


class ThreadScopeResponse(BaseModel):
    paper_ids: List[str] = Field(default_factory=list)
    papers: List[IndexedPaper] = Field(default_factory=list)


class IndexArxivRequest(BaseModel):
    url: str = Field(..., min_length=10)


class IndexArxivResponse(BaseModel):
    paper_id: str
    status: str


# ── Researchers ─────────────────────────────────────────────────────────────


class ResearcherResult(BaseModel):
    name: str
