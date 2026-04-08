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
    """

    def __init__(self, tools: list[BaseTool]):
        self._client  = AsyncGroq(api_key=settings.groq_api_key)
        self._model   = settings.groq_model
        self._tools   = {tool.name: tool for tool in tools}
        self._schemas = [tool.to_groq_schema() for tool in tools]

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

        max_iterations = settings.agent_max_iterations
        iteration      = 0

        try:
            while iteration < max_iterations:
                iteration += 1
                logger.info(f"[TOKENS] Agent loop iteration {iteration}/{max_iterations}")

                is_last_iteration = (iteration == max_iterations)

                response = await self._client.chat.completions.create(
                    model       = self._model,
                    messages    = groq_messages,
                    tools       = self._schemas,
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
                    # Log final cumulative totals for this full request
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

            # Safety net — last iteration forces tool_choice=none so this
            # should never be reached, but log totals anyway
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