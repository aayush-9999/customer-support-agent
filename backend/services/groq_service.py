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
_TOOL_CALLS_MARKER = "__tool_calls__:"


class GroqService(LLMBase):
    """
    Groq implementation of LLMBase.

    With the meta-tool architecture, this service is significantly simpler:
    - No schema pruning (_prune_schemas removed — not needed with 2 schemas)
    - No session state extraction (_extract_session_state removed)
    - The LLM always gets exactly 2 tool schemas (tool_search + tool_invoke)
    - Tool invocations still go through the same agentic loop

    Token logging is preserved and enhanced:
    - Logs actual Groq usage per iteration (prompt, completion, total)
    - Logs cumulative totals at end of request
    - tool_invoke results are tagged with _invoked_tool for history slimming

    Flow per request:
      Iterative loop:
        1. Send messages + 2 meta-tool schemas to Groq
        2. If model calls think → execute, loop
        3. If model calls tool_search → execute (returns matching real schemas)
        4. If model calls tool_invoke → execute real tool, tag result, loop
        5. If model returns text → done
    """

    def __init__(self, tools: list[BaseTool]):
        self._client  = AsyncGroq(api_key=settings.groq_api_key)
        self._model   = settings.groq_model
        self._tools   = {tool.name: tool for tool in tools}
        self._schemas = [tool.to_groq_schema() for tool in tools]

        logger.info(
            f"[GROQ] GroqService init — {len(self._schemas)} schemas "
            f"(meta-tool mode: schemas are always tool_search + tool_invoke)"
        )

    async def chat(
        self,
        messages:      list[Message],
        tools:         list[BaseTool],
        system_prompt: str,
        session_id: str  = None 
    ) -> AgentResponse:

        groq_messages = self._build_messages(messages, system_prompt)
        self._session_id = session_id  

        all_tool_calls:   list[ToolCall]   = []
        all_tool_results: list[ToolResult] = []

        # ── Per-request token accumulators ────────────────────────────────────
        total_prompt_tokens     = 0
        total_completion_tokens = 0
        total_tokens_used       = 0

        max_iterations         = settings.agent_max_iterations
        iteration              = 0
        consecutive_think_only = 0

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

                # ── No tool calls → model is done ─────────────────────────
                if not message.tool_calls:
                    # Log final cumulative totals for this full request
                    logger.info(
                        f"[TOKENS] ══ REQUEST COMPLETE ══ "
                        f"iterations: {iteration} | "
                        f"prompt tokens: {total_prompt_tokens} | "
                        f"completion tokens: {total_completion_tokens} | "
                        f"TOTAL: {total_tokens_used}"
                    )
                    return AgentResponse(
                        message      = message.content or "",
                        tool_calls   = all_tool_calls,
                        tool_results = all_tool_results,
                    )

                # ── Tool calls present → execute and loop ──────────────────
                groq_messages.append({
                    "role":       "assistant",
                    "content":    None,
                    "tool_calls": message.tool_calls,
                })

                result_dicts, tool_calls_made, tool_results_made = (
                    await self._execute_tool_calls(message.tool_calls)
                )
                all_tool_calls.extend(tool_calls_made)
                all_tool_results.extend(tool_results_made)
                groq_messages.extend(result_dicts)

                # ── Consecutive think-only guard ───────────────────────────
                real_tools_this_iter = [
                    tc for tc in tool_calls_made
                    if tc.tool_name not in ("think", "tool_search")
                ]
                if not real_tools_this_iter:
                    consecutive_think_only += 1
                    logger.info(
                        f"[TOKENS] Think/search-only iteration "
                        f"{consecutive_think_only}/3 (no data tool executed)"
                    )
                    if consecutive_think_only >= 3:
                        logger.warning(
                            "[LOOP] 3 consecutive think/search-only iterations — "
                            "injecting nudge and forcing text reply"
                        )
                        groq_messages.append({
                            "role": "user",
                            "content": (
                                "[SYSTEM] You have called think or tool_search 3 times "
                                "without calling tool_invoke. You MUST now either: "
                                "(a) call tool_invoke directly with a confirmed tool_id, or "
                                "(b) reply to the customer explaining what you can and cannot do. "
                                "Do NOT call think or tool_search again."
                            )
                        })
                        final_resp = await self._client.chat.completions.create(
                            model       = self._model,
                            messages    = groq_messages,
                            tools       = self._schemas,
                            tool_choice = "none",
                            temperature = settings.groq_temperature,
                            max_tokens  = settings.groq_max_tokens,
                        )
                        final_content = (
                            final_resp.choices[0].message.content
                            or "I'm sorry, I wasn't able to process that. Please try again."
                        )
                        logger.info(
                            f"[TOKENS] ══ REQUEST COMPLETE (think-loop broken) ══ "
                            f"TOTAL: {total_tokens_used}"
                        )
                        return AgentResponse(
                            message      = final_content,
                            tool_calls   = all_tool_calls,
                            tool_results = all_tool_results,
                        )
                else:
                    consecutive_think_only = 0

                logger.info(
                    f"[TOKENS] Iteration {iteration}: called "
                    f"{[tc.tool_name for tc in tool_calls_made]}, looping..."
                )

            # Safety net — last iteration forces tool_choice=none so this
            # should never be reached, but log totals anyway
            logger.info(
                f"[TOKENS] ══ REQUEST COMPLETE (max iterations) ══ "
                f"prompt: {total_prompt_tokens} | "
                f"completion: {total_completion_tokens} | "
                f"TOTAL: {total_tokens_used}"
            )
            return AgentResponse(
                message      = "I wasn't able to complete that in time. Please try again.",
                tool_calls   = all_tool_calls,
                tool_results = all_tool_results,
            )

        except Exception as e:
            logger.exception("GroqService.chat failed")
            return AgentResponse(
                message=(
                    "I'm having trouble connecting right now. "
                    "Please try again in a moment."
                ),
                error=str(e),
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
        """
        Execute all tool calls returned by the model in this iteration.

        For tool_invoke specifically:
        - The result from ToolInvokeTool already has _invoked_tool tagged.
        - We store this tag in the result content so that:
          a) conversation_store._slim_tool_result can apply the right slimming
          b) loop.py _build_history_summary can show the real tool name, not "tool_invoke"

        The tag is stored in JSON but is harmless — the LLM ignores unknown fields.
        """
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
                logger.info(f"Executing tool: {tool_name} with args: {list(arguments.keys())}")
                if tool_name == "tool_invoke" and self._session_id:
                    arguments["session_id"] = self._session_id
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