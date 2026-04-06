# backend/database_pg.py

import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from backend.core.config import get_settings
from backend.models.base import Base

logger   = logging.getLogger(__name__)
settings = get_settings()

engine       = None
SessionLocal = None


async def connect_pg() -> None:
    global engine, SessionLocal

    if settings.db_tool_mode != "postgres":
        logger.info("DB_TOOL_MODE is not postgres — skipping PostgreSQL.")
        return

    engine = create_async_engine(
        settings.postgres_uri,
        pool_size = settings.postgres_max_connections,
        echo      = settings.debug,
    )
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info(f"PostgreSQL connected — db: {settings.postgres_db}")


async def disconnect_pg() -> None:
    global engine
    if engine:
        await engine.dispose()
        engine = None
        logger.info("PostgreSQL disconnected")


async def get_pg_session():
    if SessionLocal is None:
        yield None
        return
    async with SessionLocal() as session:
        yield session