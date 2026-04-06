import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import get_settings
from backend.core.container import init_container
from backend.database import connect_db, disconnect_db, get_db
from backend.database_pg import connect_pg, disconnect_pg
from backend.api.websocket import ws_manager

settings = get_settings()

logging.basicConfig(
    level  = getattr(logging, settings.log_level),
    format = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.app_name} [{settings.environment}]")
    logger.info(f"Config: {settings.redacted_summary()}")

    await connect_db()
    init_container(get_db())

    if settings.postgres_uri:
        await connect_pg()
    else:
        logger.info("PostgreSQL not configured — skipping.")

    logger.info("Application ready.")
    yield

    logger.info("Shutting down...")
    await disconnect_db()

    if settings.postgres_uri:
        await disconnect_pg()


app = FastAPI(
    title    = settings.app_name,
    version  = "0.1.0",
    lifespan = lifespan,
    docs_url = "/docs",
    redoc_url = None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

from backend.api.routes import router
app.include_router(router, prefix="/api")

from backend.api.auth import router as auth_router
app.include_router(auth_router, prefix="/api")

from backend.api.admin import router as admin_router
app.include_router(admin_router, prefix="/api")

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await ws_manager.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(session_id)

@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name}