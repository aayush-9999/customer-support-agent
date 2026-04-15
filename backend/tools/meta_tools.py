# backend/tools/meta_tools.py

import logging
from typing import Any
from backend.tools.base import BaseTool
from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Tokens saved per turn vs. sending all schemas.
# Logged so you can verify savings in your existing token logging.
_TOKENS_PER_SCHEMA = 250


class ToolSearchTool(BaseTool):
    """
    Meta-tool #1: Semantic search over the full tool registry.

    The LLM calls this when it recognises it needs data or an action.
    It receives up to 3 matching tool schemas — the only schemas that
    ever enter the context window — and then calls tool_invoke.

    Token cost: ~250 tokens (this schema) + ~750 tokens (3 returned schemas)
    vs. all schemas every call.
    """

    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    @property
    def name(self) -> str:
        return "tool_search"

    @property
    def description(self) -> str:
        return (
            "Search for available tools that can handle a specific task. "
            "Call this BEFORE invoking any data or action tool. "
            "Returns up to 3 matching tools with their exact parameter schemas. "
            "Examples: 'get customer order history by email', "
            "'change delivery date for order', 'initiate return for delivered order'."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language description of what you need to do. "
                        "Be specific: include the entity (order, user, return) and action."
                    )
                }
            },
            "required": ["query"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        query = kwargs.get("query", "").strip()
        if not query:
            return self.error("query is required.")

        results = self._registry.search(query, top_n=3)

        total_in_registry = self._registry.tool_count()
        schemas_saved     = total_in_registry - len(results)
        tokens_saved      = schemas_saved * _TOKENS_PER_SCHEMA

        logger.info(
            f"[TOOL_SEARCH] query='{query[:60]}' → "
            f"returned {len(results)}/{total_in_registry} schemas "
            f"(~{tokens_saved} tokens saved this call)"
        )

        return self.success({
            "available_tools": results,
            "instruction": (
                "Review the tools above. "
                "Select the best match and call tool_invoke with its tool_id "
                "and all required arguments. "
                "Do NOT call tool_search again unless none of these tools fit."
            ),
        })


class ToolInvokeTool(BaseTool):
    """
    Meta-tool #2: Execute a tool from the registry by ID.

    The LLM calls this after tool_search has returned the right tool_id.
    The result is identical to what calling the real tool directly would return —
    the rest of the system (history storage, schema pruning) sees the same data.
    """

    def __init__(self, registry: ToolRegistry):
        self._registry = registry
        self._session_id = None

    @property
    def name(self) -> str:
        return "tool_invoke"

    @property
    def description(self) -> str:
        return (
            "Invoke a specific tool by its tool_id. "
            "You MUST have the exact tool_id from a prior tool_search result. "
            "Pass all required arguments as per the schema returned by tool_search."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tool_id": {
                    "type": "string",
                    "description": (
                        "The exact tool_id string returned by tool_search. "
                        "Never guess or construct a tool_id — only use what tool_search returned."
                    )
                },
                # In ToolInvokeTool.parameters, replace the arguments property with:
                "arguments": {
                    "type": "object",
                    "description": (
                        "Key-value pairs matching the tool's required parameters. "
                        "Use the parameter schema from tool_search to know what to pass."
                    ),
                    "additionalProperties": True
                }
            },
            "required": ["tool_id", "arguments"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        tool_id   = kwargs.get("tool_id", "").strip()
        arguments = kwargs.get("arguments", {})
        session_id = kwargs.get("session_id")             # ← add this line
        if session_id:
            arguments["session_id"] = session_id 

        if not tool_id:
            return self.error("tool_id is required.")

        if not isinstance(arguments, dict):
            return self.error("arguments must be a JSON object (dict).")

        tool = self._registry.get_tool(tool_id)
        if not tool:
            available = self._registry.all_tool_names()
            return self.error(
                f"Unknown tool_id '{tool_id}'. "
                f"Valid tool_ids are: {available}. "
                f"Call tool_search first to get the correct tool_id."
            )
        
        if self._session_id and "session_id" not in arguments:
            arguments["session_id"] = self._session_id

        logger.info(f"[TOOL_INVOKE] tool_id={tool_id}, args={list(arguments.keys())}")

        # Execute the real tool — same as if the LLM called it directly
        result = await tool.execute(**arguments)

        # Tag the result with the real tool name so that history slimming
        # in conversation_store._slim_tool_result can apply the right logic.
        # This tag is stripped before returning to the LLM.
        if isinstance(result, dict):
            result["_invoked_tool"] = tool_id

        return result