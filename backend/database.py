# backend/database.py

import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from backend.core.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

_client: AsyncIOMotorClient | None = None


async def connect_db() -> None:
    global _client

    if settings.db_tool_mode != "mongo":
        logger.info("DB_TOOL_MODE is not mongo — skipping MongoDB.")
        return

    _client = AsyncIOMotorClient(
        settings.mongo_uri,
        serverSelectionTimeoutMS=settings.mongo_connect_timeout_ms,
        tls=True,
        tlsAllowInvalidCertificates=True,
    )
    await _client.admin.command("ping")
    logger.info(f"MongoDB connected — db: {settings.mongo_db_name}")


async def disconnect_db() -> None:
    global _client
    if _client:
        _client.close()
        _client = None
        logger.info("MongoDB disconnected")


def get_db():
    if settings.db_tool_mode != "mongo":
        return None
    if _client is None:
        raise RuntimeError("Database not connected. Call connect_db() first.")
    return _client[settings.mongo_db_name]