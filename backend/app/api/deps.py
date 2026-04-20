"""Shared FastAPI dependencies."""

from __future__ import annotations

from app.core.security import get_current_user  # re-export

__all__ = ["get_current_user"]
