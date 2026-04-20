"""Auth routes: register & login."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.schemas import AuthResponse, LoginRequest, RegisterRequest
from app.core.security import create_token, hash_password, verify_password
from app.db.engine import get_session
from app.db.models import AppUser

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest) -> AuthResponse:
    async with get_session() as db:
        async with db.begin():
            existing = (await db.execute(
                select(AppUser.user_id).where(AppUser.email == req.email)
            )).scalar_one_or_none()
            if existing:
                raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

            user_id = str(uuid.uuid4())
            db.add(AppUser(user_id=user_id, email=req.email, hashed_password=hash_password(req.password)))

    return AuthResponse(user_id=user_id, token=create_token(user_id))


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest) -> AuthResponse:
    async with get_session() as db:
        async with db.begin():
            row = (await db.execute(
                select(AppUser.user_id, AppUser.hashed_password).where(AppUser.email == req.email)
            )).one_or_none()

    if not row or not verify_password(req.password, row.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    return AuthResponse(user_id=row.user_id, token=create_token(row.user_id))
