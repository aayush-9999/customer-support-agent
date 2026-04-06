import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from backend.core.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

engine       = None
SessionLocal = None


class Base(DeclarativeBase):
    pass


async def connect_pg() -> None:
    global engine, SessionLocal

    engine = create_async_engine(
        settings.postgres_uri,
        pool_size    = settings.postgres_max_connections,
        echo         = settings.debug,
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


async def get_pg_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session