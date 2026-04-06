# backend/api/dependencies.py

import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bson import ObjectId

from backend.core.config import get_settings
from backend.core.security import decode_token
from backend.core.container import get_container
from backend.database import get_db
from backend.database_pg import get_pg_session
from backend.models.user import User
from backend.services.conversation_store import ConversationStore
from backend.tools.base import BaseTool

logger   = logging.getLogger(__name__)
settings = get_settings()
bearer   = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db:          AsyncIOMotorDatabase         = Depends(get_db),
    session:     AsyncSession                 = Depends(get_pg_session),
) -> dict:
    token   = credentials.credentials
    payload = decode_token(token)

    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    if settings.db_tool_mode == "postgres":
        result = await session.execute(
            select(User).where(User.id == payload.get("sub"))
        )
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
        return {
            "_id":           user.id,
            "email":         user.email,
            "name":          user.name,
            "surname":       user.surname,
            "role":          user.role,
            "loyaltyTier":   user.loyalty_tier,
            "loyaltyPoints": user.loyalty_points,
            "accountStatus": user.account_status,
        }

    # Mongo
    user_id = payload.get("sub")
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")

    user = await db.users.find_one({"_id": oid})
    if not user or not user.get("isActive", True):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    user["_id"] = str(user["_id"])
    return user


async def get_current_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def get_groq():
    return get_container().groq

def get_policy():
    return get_container().policy

def get_conversations() -> ConversationStore:
    return get_container().conversations

def get_tools() -> list[BaseTool]:
    return get_container().tools