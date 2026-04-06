import logging
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.tools.mongo_tools import get_all_tools
from backend.tools.pg_tools import get_all_pg_tools
from backend.services.groq_service import GroqService
from backend.policies.file_store import FilePolicyStore
from backend.services.conversation_store import ConversationStore
from backend.database_pg import SessionLocal

logger = logging.getLogger(__name__)


class Container:
    def __init__(self, db: AsyncIOMotorDatabase):
        logger.info("Initialising container...")

        mongo_tools = get_all_tools(db)

        pg_tools = get_all_pg_tools(SessionLocal) if SessionLocal is not None else []

        all_tools = mongo_tools + pg_tools

        self.groq          = GroqService(all_tools)
        self.policy        = FilePolicyStore()
        self.conversations = ConversationStore(db)

        logger.info(
            f"Container ready — "
            f"{len(all_tools)} tools loaded "
            f"({len(mongo_tools)} mongo, {len(pg_tools)} pg)"
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