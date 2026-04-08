# backend/services/groq_service.py

import json
import logging
from typing import Any

from groq import AsyncGroq

from backend.agent.schemas import AgentResponse, Message, Role, ToolCall, ToolResult
from backend.core.config import get_settings
from backend.services.llm_base import LLMBase
from backend.tools.base import BaseTool

logger   = logging.getLogger(__name__)
settings = get_settings()

# Marker prefix used to encode tool_call history rows as assistant messages.
# routes.py writes these when rebuilding history; _build_messages() decodes them.
_TOOL_CALLS_MARKER = "__tool_calls__:"

# Tools that are session-state-prunable:
# if we can determine from history that they're irrelevant, we drop their schema.
_TERMINAL_STATUSES = {"Delivered", "Completed", "Cancelled"}


def _extract_session_state(messages: list[dict]) -> dict:
    """
    Scan the groq_messages list (already built) to derive session state.
    Returns a dict with keys that the pruner checks:
      - order_ids_seen: set of order IDs that appeared in tool results
      - profile_fetched: bool — get_user_profile was already called
      - order_statuses: dict[order_id -> status] from get_order_details results
      - return_topics_mentioned: bool — any return/refund topic appeared
    """
    state = {
        "order_ids_seen":        set(),
        "profile_fetched":       False,
        "order_statuses":        {},   # order_id -> status string
        "return_topics_mentioned": False,
    }

    for msg in messages:
        role = msg.get("role")

        # Scan tool results for data already fetched
        if role == "tool":
            try:
                result = json.loads(msg.get("content", "{}"))
                if not result.get("success"):
                    continue
                data = result.get("data", {})

                # get_order_history result
                if "orders" in data:
                    for order in data["orders"]:
                        oid = order.get("order_id", "")
                        if oid:
                            state["order_ids_seen"].add(oid)

                # get_order_details result
                if "_id" in data and "status" in data:
                    oid = data["_id"]
                    state["order_ids_seen"].add(oid)
                    state["order_statuses"][oid] = data.get("status", "")

                # get_user_profile result
                if "email" in data and "loyaltyTier" in data:
                    state["profile_fetched"] = True

            except Exception:
                pass

        # Scan user messages for return/refund keywords
        if role == "user":
            content = (msg.get("content") or "").lower()
            if any(kw in content for kw in ["return", "refund", "exchange", "send back"]):
                state["return_topics_mentioned"] = True

    return state


def _prune_schemas(all_schemas: list[dict], state: dict) -> list[dict]:
    """
    Given session state, return a filtered list of tool schemas.
    Only removes schemas that are provably irrelevant — never removes
    a tool that might still be needed.

    Pruning rules:
      - get_order_history: remove if we already have order history (order_ids_seen non-empty)
      - get_user_profile: remove if profile was already fetched this session
      - change_delivery_date / change_delivery_address: remove if ALL seen orders are terminal
      - get_return_status: remove if no return-related topic has been mentioned at all
    """
    order_ids   = state["order_ids_seen"]
    statuses    = state["order_statuses"]

    # Check if every known order is in a terminal state
    all_terminal = (
        bool(order_ids) and
        all(statuses.get(oid, "") in _TERMINAL_STATUSES for oid in order_ids)
    )

    pruned = []
    removed = []

    for schema in all_schemas:
        tool_name = schema.get("function", {}).get("name", "")

        if tool_name == "get_order_history" and order_ids:
            removed.append(tool_name)
            continue

        if tool_name == "get_user_profile" and state["profile_fetched"]:
            removed.append(tool_name)
            continue

        if tool_name in ("change_delivery_date", "change_delivery_address") and all_terminal:
            removed.append(tool_name)
            continue

        if tool_name == "get_return_status" and not state["return_topics_mentioned"]:
            removed.append(tool_name)
            continue

        pruned.append(schema)

    if removed:
        schema_tokens_saved = len(removed) * 250  # ~250 tokens per schema × 2 iters
        logger.info(
            f"[TOKENS] Schema pruning — removed {len(removed)} schemas "
            f"({removed}) → saved ~{schema_tokens_saved * 2} tokens this turn"
        )
    else:
        logger.info(f"[TOKENS] Schema pruning — no schemas removed ({len(all_schemas)} kept)")

    return pruned


class GroqService(LLMBase):
    """
    Groq implementation of LLMBase.

    Flow per request:
      Iterative loop — send messages with tool schemas → model may call tools
      → execute tools → feed results back → repeat until model writes final reply
      or max_iterations is hit.

    Token logging:
      Every Groq API call returns usage.prompt_tokens / completion_tokens / total_tokens.
      We log these per iteration AND accumulate totals for the full request so you
      can see exactly what each conversation turn costs.

    Schema pruning:
      After building the message list, we analyse session state from the history
      and strip tool schemas that are provably irrelevant for this turn. Going from
      10 schemas to 5-6 saves ~1,000-1,500 tokens per iteration → ~2,000-3,000 per turn.
    """

    def __init__(self, tools: list[BaseTool]):
        self._client  = AsyncGroq(api_key=settings.groq_api_key)
        self._model   = settings.groq_model
        self._tools   = {tool.name: tool for tool in tools}
        self._schemas = [tool.to_groq_schema() for tool in tools]

        # Log schema token cost so we have a baseline
        schema_token_est = len(self._schemas) * 250
        logger.info(
            f"[TOKENS] GroqService init — {len(self._schemas)} tool schemas "
            f"(~{schema_token_est} tokens each API call if unpruned)"
        )

    async def chat(
        self,
        messages:      list[Message],
        tools:         list[BaseTool],
        system_prompt: str,
    ) -> AgentResponse:

        groq_messages     = self._build_messages(messages, system_prompt)
        all_tool_calls:   list[ToolCall]   = []
        all_tool_results: list[ToolResult] = []

        # ── Per-request token accumulators ────────────────────────────────────
        total_prompt_tokens     = 0
        total_completion_tokens = 0
        total_tokens_used       = 0

        # ── Prune schemas based on session state ─────────────────────────────
        # Do this ONCE before the loop — state doesn't change between iterations
        # because new tool results are appended within this request's groq_messages,
        # not from the persisted DB history. We'll re-derive after each tool call
        # within the loop by passing the live groq_messages.
        active_schemas = _prune_schemas(self._schemas, _extract_session_state(groq_messages))

        max_iterations = settings.agent_max_iterations
        iteration      = 0

        try:
            while iteration < max_iterations:
                iteration += 1
                logger.info(f"[TOKENS] Agent loop iteration {iteration}/{max_iterations}")

                is_last_iteration = (iteration == max_iterations)

                # Re-prune on every iteration so schemas added mid-turn
                # (e.g. after get_order_history returns, we can drop it next iter)
                if iteration > 1:
                    active_schemas = _prune_schemas(
                        self._schemas,
                        _extract_session_state(groq_messages)
                    )

                # Estimate schema tokens for this iteration
                schema_token_est = len(active_schemas) * 250
                logger.info(
                    f"[TOKENS] Iteration {iteration}: sending {len(active_schemas)} schemas "
                    f"(~{schema_token_est} tokens)"
                )

                response = await self._client.chat.completions.create(
                    model       = self._model,
                    messages    = groq_messages,
                    tools       = active_schemas,
                    tool_choice = "none" if is_last_iteration else "auto",
                    temperature = settings.groq_temperature,
                    max_tokens  = settings.groq_max_tokens,
                )

                # ── Log token usage for this API call ─────────────────────────
                usage = response.usage
                if usage:
                    iter_prompt     = usage.prompt_tokens
                    iter_completion = usage.completion_tokens
                    iter_total      = usage.total_tokens

                    total_prompt_tokens     += iter_prompt
                    total_completion_tokens += iter_completion
                    total_tokens_used       += iter_total

                    logger.info(
                        f"[TOKENS] Iteration {iteration} — "
                        f"prompt: {iter_prompt} | "
                        f"completion: {iter_completion} | "
                        f"total: {iter_total}"
                    )

                choice  = response.choices[0]
                message = choice.message

                # ── No tool calls → model is done ─────────────────────────────
                if not message.tool_calls:
                    logger.info(
                        f"[TOKENS] ══ REQUEST COMPLETE ══ "
                        f"iterations: {iteration} | "
                        f"total prompt tokens: {total_prompt_tokens} | "
                        f"total completion tokens: {total_completion_tokens} | "
                        f"TOTAL TOKENS USED: {total_tokens_used}"
                    )
                    return AgentResponse(
                        message      = message.content or "",
                        tool_calls   = all_tool_calls,
                        tool_results = all_tool_results,
                    )

                # ── Tool calls present → execute and loop ─────────────────────
                groq_messages.append({
                    "role":       "assistant",
                    "content":    None,
                    "tool_calls": message.tool_calls,
                })

                tool_result_dicts, tool_calls_made, tool_results_made = (
                    await self._execute_tool_calls(message.tool_calls)
                )
                all_tool_calls.extend(tool_calls_made)
                all_tool_results.extend(tool_results_made)
                groq_messages.extend(tool_result_dicts)

                logger.info(
                    f"[TOKENS] Iteration {iteration}: called "
                    f"{[tc.tool_name for tc in tool_calls_made]}, looping..."
                )

            logger.info(
                f"[TOKENS] ══ REQUEST COMPLETE (max iterations) ══ "
                f"total prompt tokens: {total_prompt_tokens} | "
                f"total completion tokens: {total_completion_tokens} | "
                f"TOTAL TOKENS USED: {total_tokens_used}"
            )
            return AgentResponse(
                message      = "I wasn't able to complete that in time. Please try again.",
                tool_calls   = all_tool_calls,
                tool_results = all_tool_results,
            )

        except Exception as e:
            logger.exception("GroqService.chat failed")
            return AgentResponse(
                message = (
                    "I'm having trouble connecting right now. "
                    "Please try again in a moment."
                ),
                error = str(e),
            )

    # ── Private ────────────────────────────────────────────────────────────────

    def _build_messages(
        self,
        messages:      list[Message],
        system_prompt: str,
    ) -> list[dict]:
        """
        Convert Message objects to Groq's dict format.

        Special case: assistant messages whose content starts with
        __tool_calls__: are encoded tool history rows from conversation_store.
        We decode them back into the Groq tool_calls structure so the
        model sees its previous tool invocations in the correct format.
        """
        groq_messages = [{"role": "system", "content": system_prompt}]

        for msg in messages:
            if msg.role == Role.tool:
                groq_messages.append({
                    "role":         "tool",
                    "content":      msg.content,
                    "tool_call_id": msg.tool_call_id,
                })

            elif msg.role == Role.assistant:
                if msg.content and msg.content.startswith(_TOOL_CALLS_MARKER):
                    payload_str = msg.content[len(_TOOL_CALLS_MARKER):]
                    try:
                        payload = json.loads(payload_str)
                        fake_tool_calls = [
                            {
                                "id":   tc["id"],
                                "type": "function",
                                "function": {
                                    "name":      tc["name"],
                                    "arguments": json.dumps(tc["arguments"]),
                                },
                            }
                            for tc in payload
                        ]
                        groq_messages.append({
                            "role":       "assistant",
                            "content":    None,
                            "tool_calls": fake_tool_calls,
                        })
                    except Exception:
                        logger.warning("Failed to decode __tool_calls__ history row")
                else:
                    groq_messages.append({
                        "role":    "assistant",
                        "content": msg.content,
                    })

            elif msg.role == Role.user:
                groq_messages.append({
                    "role":    "user",
                    "content": msg.content,
                })

        return groq_messages

    async def _execute_tool_calls(
        self,
        tool_calls: list[Any],
    ) -> tuple[list[dict], list[ToolCall], list[ToolResult]]:
        result_dicts     = []
        tool_calls_out   = []
        tool_results_out = []

        for tc in tool_calls:
            tool_name = tc.function.name
            tool_id   = tc.id

            try:
                arguments = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                arguments = {}
                logger.warning(f"Could not parse arguments for tool '{tool_name}'")

            tool = self._tools.get(tool_name)
            if not tool:
                result = {"success": False, "error": f"Unknown tool: {tool_name}"}
                logger.warning(f"Groq called unknown tool: {tool_name}")
            else:
                logger.info(f"Executing tool: {tool_name} with args: {arguments}")
                result = await tool.execute(**arguments)

            result_content = json.dumps(result)

            tool_calls_out.append(ToolCall(
                id        = tool_id,
                tool_name = tool_name,
                arguments = arguments,
            ))
            tool_results_out.append(ToolResult(
                tool_call_id = tool_id,
                content      = result_content,
            ))
            result_dicts.append({
                "role":         "tool",
                "tool_call_id": tool_id,
                "content":      result_content,
            })

        return result_dicts, tool_calls_out, tool_results_out