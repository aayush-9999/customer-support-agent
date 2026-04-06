# backend/api/websocket.py

import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Manages active WebSocket connections.

    Two channels:
      - Customer sessions  (session_id → WebSocket)
        Used when admin approves/rejects: push notification to the customer.

      - Admin CRM sessions (admin_id → WebSocket)
        Used when a new pending request is created: broadcast to all CRM tabs
        so they reload immediately instead of waiting for a polling interval.
    """

    def __init__(self):
        # customer sessions: session_id → WebSocket
        self._connections: dict[str, WebSocket] = {}

        # admin sessions: arbitrary key (e.g. admin email or uuid) → WebSocket
        self._admin_connections: dict[str, WebSocket] = {}

    # ── Customer sessions ──────────────────────────────────────────────────────

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[session_id] = websocket
        logger.info(f"WebSocket connected — session={session_id}")

    def disconnect(self, session_id: str) -> None:
        self._connections.pop(session_id, None)
        logger.info(f"WebSocket disconnected — session={session_id}")

    async def notify_session(self, session_id: str, payload: dict) -> bool:
        """
        Send a message to a specific customer session if online.
        Returns True if delivered, False if session not connected.
        """
        ws = self._connections.get(session_id)
        if not ws:
            return False
        try:
            await ws.send_json(payload)
            logger.info(f"WebSocket notification sent — session={session_id}")
            return True
        except Exception as e:
            logger.warning(f"WebSocket send failed — session={session_id}: {e}")
            self.disconnect(session_id)
            return False

    def is_online(self, session_id: str) -> bool:
        return session_id in self._connections

    # ── Admin CRM sessions ─────────────────────────────────────────────────────

    async def connect_admin(self, admin_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._admin_connections[admin_id] = websocket
        logger.info(f"Admin WebSocket connected — admin={admin_id}")

    def disconnect_admin(self, admin_id: str) -> None:
        self._admin_connections.pop(admin_id, None)
        logger.info(f"Admin WebSocket disconnected — admin={admin_id}")

    async def broadcast_to_admins(self, payload: dict) -> int:
        """
        Push a message to ALL connected admin CRM sessions.
        Returns the count of successfully notified admins.
        Called when a new pending request is created so the CRM
        refreshes immediately instead of waiting for a poll interval.
        """
        dead = []
        notified = 0
        for admin_id, ws in self._admin_connections.items():
            try:
                await ws.send_json(payload)
                notified += 1
            except Exception as e:
                logger.warning(
                    f"Admin WebSocket send failed — admin={admin_id}: {e}"
                )
                dead.append(admin_id)

        for admin_id in dead:
            self.disconnect_admin(admin_id)

        if notified:
            logger.info(
                f"Admin broadcast sent — type={payload.get('type')} "
                f"notified={notified}"
            )
        return notified


# Module-level singleton
ws_manager = WebSocketManager()