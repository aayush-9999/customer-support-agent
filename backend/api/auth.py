import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.core.security import (
    verify_password,
    hash_password,
    create_access_token,
)
from backend.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email:    EmailStr
    password: str = Field(..., min_length=6)


class RegisterRequest(BaseModel):
    name:     str      = Field(..., min_length=1)
    surname:  str      = Field(..., min_length=1)
    email:    EmailStr
    password: str      = Field(..., min_length=6)
    phone:    str | None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user: dict


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    db:      AsyncIOMotorDatabase = Depends(get_db),
):
    user = await db.users.find_one(
        {"email": {"$regex": f"^{payload.email}$", "$options": "i"}}
    )

    if not user or not user.get("password"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(payload.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.get("isActive", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please contact support.",
        )

    token = create_access_token({
        "sub":   str(user["_id"]),
        "email": user["email"],
        "role":  user.get("role", "customer"),
    })

    return AuthResponse(
        access_token=token,
        user={
            "id":           str(user["_id"]),
            "name":         user.get("name"),
            "surname":      user.get("surname"),
            "email":        user.get("email"),
            "role":         user.get("role", "customer"),
            "loyaltyTier":  user.get("loyaltyTier"),
            "loyaltyPoints": user.get("loyaltyPoints"),
            "accountStatus": user.get("accountStatus"),
        }
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    db:      AsyncIOMotorDatabase = Depends(get_db),
):
    # Check duplicate email
    existing = await db.users.find_one(
        {"email": {"$regex": f"^{payload.email}$", "$options": "i"}}
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    from datetime import datetime, timezone
    new_user = {
        "name":            payload.name,
        "surname":         payload.surname,
        "email":           payload.email.lower(),
        "password":        hash_password(payload.password),
        "phone":           payload.phone,
        "role":            "customer",
        "isActive":        True,
        "accountStatus":   "active",
        "loyaltyTier":     "Bronze",
        "loyaltyPoints":   50,   # welcome bonus
        "address":         {},
        "lastRecommendations": [],
        "createdAt":       datetime.now(timezone.utc),
    }

    result = await db.users.insert_one(new_user)
    new_user["_id"] = result.inserted_id

    token = create_access_token({
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


@router.get("/me")
async def get_me(
    db: AsyncIOMotorDatabase = Depends(get_db),
    # We'll add the current_user dependency after dependencies.py is updated
):
    return {"message": "Auth working"}

