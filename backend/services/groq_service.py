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

_ROUND2_MAX_TOKENS = 600

# Marker prefix used to encode tool_call history rows as assistant messages.
# routes.py writes these when rebuilding history; _build_messages() decodes them.
_TOOL_CALLS_MARKER = "__tool_calls__:"


class GroqService(LLMBase):
    """
    Groq implementation of LLMBase.

    Flow per request:
      Round 1 — send full context with tool schemas → model may call tools
      Round 2 — send tool results → model writes final reply

    History replay:
      When a previous turn used tools, routes.py encodes those tool calls as
      assistant messages prefixed with __tool_calls__:. _build_messages()
      decodes them back into the proper Groq tool_calls structure so the model
      has full context of what it did and what the tools returned.
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

        max_iterations = settings.agent_max_iterations  # your config already has this
        iteration      = 0

        try:
            while iteration < max_iterations:
                iteration += 1
                logger.info(f"Agent loop iteration {iteration}/{max_iterations}")

                # ── Call Groq ──────────────────────────────────────────────────
                is_last_iteration = (iteration == max_iterations)

                response = await self._client.chat.completions.create(
                    model       = self._model,
                    messages    = groq_messages,
                    tools       = self._schemas,
                    # Force text reply on last iteration so we never exit without a message
                    tool_choice = "none" if is_last_iteration else "auto",
                    temperature = settings.groq_temperature,
                    max_tokens  = settings.groq_max_tokens,
                )

                choice  = response.choices[0]
                message = choice.message

                # ── No tool calls → model is done, return its reply ────────────
                if not message.tool_calls:
                    return AgentResponse(
                        message      = message.content or "",
                        tool_calls   = all_tool_calls,
                        tool_results = all_tool_results,
                    )

                # ── Tool calls present → execute them, feed results back ───────
                # Null out any narration text (smaller models leak it mid-loop)
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

                # Append tool results — loop continues, Groq sees them next round
                groq_messages.extend(tool_result_dicts)

                logger.info(
                    f"Iteration {iteration}: called "
                    f"{[tc.tool_name for tc in tool_calls_made]}, looping..."
                )

            # Should not reach here (last iteration forces tool_choice=none)
            # but safety net just in case
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
        __tool_calls__: are encoded tool history rows from routes.py.
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
                    # Decode the stored tool call back into Groq format
                    payload_str = msg.content[len(_TOOL_CALLS_MARKER):]
                    try:
                        payload = json.loads(payload_str)
                        # Reconstruct Groq-compatible tool_calls list
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
                        # Malformed payload — skip this row gracefully
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
        result_dicts   = []
        tool_calls_out = []
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