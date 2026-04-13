# backend/core/container.py

import logging
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.core.config import get_settings
from backend.services.conversation_store import ConversationStore
from backend.tools.mongo_tools import ThinkTool  # or pg_tools if postgres

logger   = logging.getLogger(__name__)
settings = get_settings()


class Container:
    """
    Dependency injection container.

    Wires together all services and tools on startup.

    Tool architecture (dynamic registry mode):
        - All real tools (GetOrderDetails, InitiateReturn, etc.) are
          registered in ToolRegistry and embedded semantically.
        - The LLM only ever sees 2 meta-tool schemas:
            tool_search  — find the right tool by description
            tool_invoke  — run the found tool
        - GroqService receives only the 2 meta-tools.
        - This keeps context window cost flat regardless of how many
          real tools exist.

    Token accounting:
        - Old: N_tools × 250 tokens × 2 iterations ≈ 5,500 tokens/turn (11 tools)
        - New: 2 × 250 tokens × (2 + 1 search iter) ≈ 1,500 tokens/turn
        - Savings grow linearly with tool count.
    """

    def __init__(self, db):
        logger.info("Initialising container...")

        # ── Step 1: Build all real tools ─────────────────────────────────────
        if settings.db_tool_mode == "mongo":
            from backend.tools.mongo_tools import get_all_tools
            real_tools = get_all_tools(db)
            self.conversations = ConversationStore(db=db)

        elif settings.db_tool_mode == "postgres":
            from backend.tools.pg_tools import get_all_pg_tools
            import backend.database_pg as pg_db
            real_tools = get_all_pg_tools(pg_db.SessionLocal)
            self.conversations = ConversationStore(db=None, session_factory=pg_db.SessionLocal)

        else:
            real_tools = []
            self.conversations = ConversationStore(db=None)
            logger.warning(f"Unknown DB_TOOL_MODE: {settings.db_tool_mode} — no tools loaded.")

        logger.info(f"[CONTAINER] {len(real_tools)} real tools loaded.")

        # ── Step 2: Build registry with semantic embeddings ───────────────────
        from backend.services.embedding_service import get_embedding_fn
        from backend.tools.registry import ToolRegistry

        embedding_fn = get_embedding_fn()       # loads model once (cached)
        self.registry = ToolRegistry(real_tools, embedding_fn)

        # ── Step 3: Build the 2 meta-tools that the LLM sees ─────────────────
        from backend.tools.meta_tools import ToolSearchTool, ToolInvokeTool

        meta_tools = [
            ThinkTool(), 
            ToolSearchTool(self.registry),
            ToolInvokeTool(self.registry),
        ]

        # ── Step 4: Build services ────────────────────────────────────────────
        from backend.services.groq_service import GroqService
        from backend.policies.file_store import FilePolicyStore

        self.groq   = GroqService(meta_tools)   # only 2 schemas to LLM
        self.policy = FilePolicyStore()
        self.tools  = meta_tools                # what agent loop uses

        logger.info(
            f"[CONTAINER] Ready — "
            f"{len(real_tools)} real tools in registry, "
            f"{len(meta_tools)} meta-tools exposed to LLM."
        )


_container = None


def init_container(db: AsyncIOMotorDatabase) -> None:
    global _container
    _container = Container(db)
    logger.info("Container initialised.")


def get_container():
    if _container is None:
        raise RuntimeError("Container not initialised.")
    return _container