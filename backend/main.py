# backend/main.py

import logging
import uuid
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
    logger.info(f"DB mode: {settings.db_tool_mode}")

    await connect_db()
    await connect_pg()
    init_container(get_db())

    logger.info("Application ready.")
    yield

    logger.info("Shutting down...")
    await disconnect_db()
    await disconnect_pg()


app = FastAPI(
    title     = settings.app_name,
    version   = "0.1.0",
    lifespan  = lifespan,
    docs_url  = "/docs",
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


# ── WebSocket endpoints ────────────────────────────────────────────────────────
#
# CRITICAL ORDER: /ws/admin MUST be declared before /ws/{session_id}.
#
# FastAPI/Starlette matches WebSocket routes in declaration order.
# If /ws/{session_id} is first, "/ws/admin" is captured as a customer session
# with session_id="admin" — the admin handler never fires, so:
#   • ws_manager._admin_connections stays empty
#   • broadcast_to_admins() notifies nobody
#   • Admin CRM never gets live updates
#   • Customer notifications also break (wrong handler registered the socket)
#
# Keeping /ws/admin first ensures the literal path wins before the wildcard.
@app.websocket("/ws/admin")
async def admin_websocket_endpoint(websocket: WebSocket):
    admin_id = str(uuid.uuid4())
    await ws_manager.connect_admin(admin_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect_admin(admin_id)
        
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