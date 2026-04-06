# backend/api/auth.py

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.core.config import get_settings
from backend.core.security import verify_password, hash_password, create_access_token
from backend.database import get_db
from backend.database_pg import get_pg_session
from backend.models.user import User

logger   = logging.getLogger(__name__)
settings = get_settings()
router   = APIRouter(prefix="/auth", tags=["auth"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email:    EmailStr
    password: str = Field(..., min_length=6)


class RegisterRequest(BaseModel):
    name:     str      = Field(..., min_length=1)
    surname:  str      = Field(..., min_length=1)
    email:    EmailStr
    password: str      = Field(..., min_length=6)
    phone:    str|None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user:         dict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pg_user_dict(user: User) -> dict:
    return {
        "id":            user.id,
        "name":          user.name,
        "surname":       user.surname,
        "email":         user.email,
        "role":          user.role,
        "loyaltyTier":   user.loyalty_tier,
        "loyaltyPoints": user.loyalty_points,
        "accountStatus": user.account_status,
    }


# ── Mongo handlers ────────────────────────────────────────────────────────────

async def _mongo_login(payload: LoginRequest, db: AsyncIOMotorDatabase) -> AuthResponse:
    user = await db.users.find_one(
        {"email": {"$regex": f"^{payload.email}$", "$options": "i"}}
    )
    if not user or not user.get("password"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not verify_password(payload.password, user["password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.get("isActive", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")

    token = create_access_token({
        "sub":   str(user["_id"]),
        "email": user["email"],
        "role":  user.get("role", "customer"),
    })
    return AuthResponse(
        access_token=token,
        user={
            "id":            str(user["_id"]),
            "name":          user.get("name"),
            "surname":       user.get("surname"),
            "email":         user.get("email"),
            "role":          user.get("role", "customer"),
            "loyaltyTier":   user.get("loyaltyTier"),
            "loyaltyPoints": user.get("loyaltyPoints"),
            "accountStatus": user.get("accountStatus"),
        }
    )


async def _mongo_register(payload: RegisterRequest, db: AsyncIOMotorDatabase) -> AuthResponse:
    existing = await db.users.find_one(
        {"email": {"$regex": f"^{payload.email}$", "$options": "i"}}
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists.")

    new_user = {
        "name":                payload.name,
        "surname":             payload.surname,
        "email":               payload.email.lower(),
        "password":            hash_password(payload.password),
        "phone":               payload.phone,
        "role":                "customer",
        "isActive":            True,
        "accountStatus":       "active",
        "loyaltyTier":         "Bronze",
        "loyaltyPoints":       50,
        "address":             {},
        "lastRecommendations": [],
        "createdAt":           datetime.now(timezone.utc),
    }
    result = await db.users.insert_one(new_user)
    token  = create_access_token({
        "sub":   str(result.inserted_id),
        "email": payload.email.lower(),
        "role":  "customer",
    })
    return AuthResponse(
        access_token=token,
        user={
            "id":            str(result.inserted_id),
            "name":          payload.name,
            "surname":       payload.surname,
            "email":         payload.email.lower(),
            "role":          "customer",
            "loyaltyTier":   "Bronze",
            "loyaltyPoints": 50,
            "accountStatus": "active",
        }
    )


# ── PG handlers ───────────────────────────────────────────────────────────────

async def _pg_login(payload: LoginRequest, session: AsyncSession) -> AuthResponse:
    result = await session.execute(
        select(User).where(User.email == payload.email.lower())
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not verify_password(payload.password, user.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")

    token = create_access_token({
        "sub":   user.id,
        "email": user.email,
        "role":  user.role,
    })
    return AuthResponse(access_token=token, user=_pg_user_dict(user))


async def _pg_register(payload: RegisterRequest, session: AsyncSession) -> AuthResponse:
    result = await session.execute(
        select(User).where(User.email == payload.email.lower())
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists.")

    user = User(
        name           = payload.name,
        surname        = payload.surname,
        email          = payload.email.lower(),
        password       = hash_password(payload.password),
        phone          = payload.phone,
        role           = "customer",
        is_active      = True,
        account_status = "active",
        loyalty_tier   = "Bronze",
        loyalty_points = 50,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_access_token({
        "sub":   user.id,
        "email": user.email,
        "role":  user.role,
    })
    return AuthResponse(access_token=token, user=_pg_user_dict(user))


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    db:      AsyncIOMotorDatabase = Depends(get_db),
    session: AsyncSession         = Depends(get_pg_session),
):
    if settings.db_tool_mode == "postgres":
        return await _pg_login(payload, session)
    return await _mongo_login(payload, db)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    db:      AsyncIOMotorDatabase = Depends(get_db),
    session: AsyncSession         = Depends(get_pg_session),
):
    if settings.db_tool_mode == "postgres":
        return await _pg_register(payload, session)
    return await _mongo_register(payload, db)


@router.get("/me")
async def get_me():
    return {"message": "Auth working"}