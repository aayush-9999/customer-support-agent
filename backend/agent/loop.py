# backend/agent/loop.py

import json
import logging
from backend.agent.schemas import AgentResponse, ChatRequest, Message, Role
from backend.services.llm_base import LLMBase
from backend.policies.file_store import FilePolicyStore
from backend.tools.base import BaseTool

logger = logging.getLogger(__name__)

# How many recent turns to keep verbatim in history.
# Older turns beyond this are compressed into a compact summary message.
# A "turn" = one user message + the agent's full response sequence.
VERBATIM_TURNS = 3

# Approximate token cost per tool schema sent to the LLM.
# Groq charges ~150–300 tokens per schema; 250 is a reasonable midpoint.
# Used only for estimation logging — not for actual billing.
_TOKENS_PER_SCHEMA = 250

SYSTEM_PROMPT_TEMPLATE = """
You are a customer support agent for Leafy, a D2C fashion and lifestyle brand.
You have access to real customer data through tools.

══ REASONING ══
Before calling any tool, call the `think` tool first to plan your approach:
  1. What is the customer asking?
  2. Do I already have that data in the conversation above?
  3. If not — which tool gets it?
  4. Do I have all required arguments right now?
Never call a data-fetching tool without calling `think` first.
Never call a tool with a guessed, invented, or placeholder argument.
Only report what a tool actually returned. If it errors, say so.
Do not narrate tool calls — call silently, reply with the result.

══ GREETINGS ══
If the customer's first message is a greeting with no question attached — greet back and ask how you can help. Do NOT call any tool on a pure greeting.

══ ORDER WORKFLOW ══
When a customer asks about "my order" without specifying which one:
  Step 1 → Call think, then get_order_history(email). Say nothing first.
  Step 2 → 0 orders: "There are no orders on this account."
            1 order: use it directly.
            2+ orders: list them (item name — date — status), ask which one.
  Step 3 → Wait for confirmation from the customer.
  Step 4 → Call think, then get_order_details(order_id) with the confirmed ID.

When a customer wants to change the delivery date:
  - Must have a confirmed order_id before calling change_delivery_date.
  - If customer says "sooner" / no specific date: call think first, then get_order_details,
    read estimated_warehouse_date, compute earliest = warehouse_date + 1 day,
    tell the customer that date and wait for confirmation.
  - Never ask the customer to supply a date they cannot know.

══ CANNOT DO ══
Cannot: upgrade shipping speed, expedite, waive fees, modify order contents,
promise delivery dates beyond what the data shows.

══ KNOWLEDGE CONTEXT ══
{knowledge_context}

══ TOOL CALLING RULES (STRICT) ══
You MUST use the provided tool calling interface when a tool is required.

CRITICAL:
- ALWAYS return tool calls using the structured tool_calls format
- NEVER return function calls in plain text
- NEVER use formats like <function>...</function>
- NEVER simulate or describe a tool call in text
- DO NOT include explanations when calling a tool

If a tool is needed:
→ Call the tool directly using tool_calls
→ Do NOT generate a normal text response

If you fail to follow this format, the response is invalid.
""".strip()


def _build_history_summary(old_messages: list[Message]) -> Message | None:
    """
    Compress old turns into a compact summary message inserted as a system-style
    context block. This is purely rule-based — no LLM call, no latency.

    Extracts:
    - What the customer asked (truncated)
    - What tools were called and key data from results
    - What the agent replied (truncated)
    """
    if not old_messages:
        return None

    turns = []
    current_turn: dict = {}

    for msg in old_messages:
        if msg.role == Role.user:
            if current_turn:
                turns.append(current_turn)
            current_turn = {
                "user":         msg.content[:120].replace("\n", " "),
                "tools_called": [],
                "tool_data":    [],
                "reply":        "",
            }

        elif msg.role == Role.assistant:
            if msg.content and msg.content.startswith("__tool_calls__:"):
                try:
                    payload = json.loads(msg.content[len("__tool_calls__:"):])
                    for tc in payload:
                        # Skip `think` in the summary — it has no useful data
                        if tc["name"] != "think":
                            current_turn.setdefault("tools_called", []).append(tc["name"])
                except Exception:
                    pass
            else:
                current_turn["reply"] = msg.content[:150].replace("\n", " ")

        elif msg.role == Role.tool:
            try:
                result = json.loads(msg.content)
                if result.get("success") and result.get("data"):
                    data = result["data"]
                    snippet = _extract_tool_snippet(msg.name or "", data)
                    if snippet:
                        current_turn.setdefault("tool_data", []).append(snippet)
            except Exception:
                pass

    if current_turn:
        turns.append(current_turn)

    if not turns:
        return None

    lines = ["[Earlier conversation summary]"]
    for i, turn in enumerate(turns, 1):
        parts = [f"Turn {i}: Customer: \"{turn.get('user', '')[:100]}\""]
        if turn.get("tools_called"):
            parts.append(f"Tools: {', '.join(turn['tools_called'])}")
        if turn.get("tool_data"):
            parts.append(f"Data: {' | '.join(turn['tool_data'])}")
        if turn.get("reply"):
            parts.append(f"Agent: \"{turn['reply'][:120]}\"")
        lines.append(" → ".join(parts))

    summary_content = "\n".join(lines)
    logger.info(
        f"[CONTEXT] History compressed: {len(old_messages)} messages → "
        f"~{max(1, len(summary_content) // 4)} tokens summary"
    )

    return Message(role=Role.user, content=summary_content)


def _extract_tool_snippet(tool_name: str, data: dict) -> str:
    """Pull the most useful facts from a tool result for the summary."""
    try:
        if tool_name == "get_order_history":
            orders = data.get("orders", [])
            if orders:
                summaries = []
                for o in orders[:3]:
                    items = ", ".join(o.get("items", [])[:2])
                    summaries.append(f"{o['order_id'][-8:]} ({items}, {o['status']})")
                return f"Orders: {' | '.join(summaries)}"

        elif tool_name == "get_order_details":
            oid    = data.get("_id", "")[-8:]
            status = data.get("status", "")
            est    = data.get("estimated_destination_date", "")[:10]
            items  = ", ".join(p.get("name", "")[:30] for p in data.get("products", [])[:2])
            return f"Order {oid}: {status}, est. {est}, items: {items}"

        elif tool_name == "get_user_profile":
            return (
                f"Customer: {data.get('name', '')} {data.get('surname', '')}, "
                f"tier: {data.get('loyaltyTier', '')}, "
                f"points: {data.get('loyaltyPoints', '')}, "
                f"status: {data.get('accountStatus', '')}"
            )

        elif tool_name == "get_return_status":
            return f"Return status: {data.get('status', '')} for order {str(data.get('orderId', ''))[-8:]}"

        elif tool_name == "change_delivery_date":
            return f"Date change outcome: {data.get('outcome', '')} for {data.get('requested_date', '')}"

        elif tool_name == "change_delivery_address":
            addr = data.get("new_address", {})
            return f"Address change: {data.get('outcome', '')} → {addr.get('city', '')}, {addr.get('country', '')}"

    except Exception:
        pass
    return ""


def _split_history_into_turns(history: list[Message]) -> list[list[Message]]:
    """
    Group messages into turns by user message boundaries.
    Returns a list of turns, each turn is a list of messages.
    """
    turns  = []
    current: list[Message] = []

    for msg in history:
        if msg.role == Role.user and current:
            turns.append(current)
            current = []
        current.append(msg)

    if current:
        turns.append(current)

    return turns


async def run_agent(
    request:      ChatRequest,
    llm:          LLMBase,
    policy_store: FilePolicyStore,
    tools:        list[BaseTool],
    history:      list[Message] | None = None,
) -> AgentResponse:

    knowledge_context = policy_store.build_context(request.message)
    system_prompt     = SYSTEM_PROMPT_TEMPLATE.format(
        knowledge_context=knowledge_context
    )

    # ── History trimming ──────────────────────────────────────────────────────
    messages: list[Message] = []

    if history:
        all_turns     = _split_history_into_turns(history)
        old_turns     = all_turns[:-VERBATIM_TURNS] if len(all_turns) > VERBATIM_TURNS else []
        recent_turns  = all_turns[-VERBATIM_TURNS:]

        old_messages    = [msg for turn in old_turns for msg in turn]
        recent_messages = [msg for turn in recent_turns for msg in turn]

        if old_messages:
            summary_msg = _build_history_summary(old_messages)
            if summary_msg:
                messages.append(summary_msg)

        messages.extend(recent_messages)

        logger.info(
            f"[CONTEXT] History: {len(all_turns)} total turns — "
            f"{len(old_turns)} compressed, {len(recent_turns)} verbatim — "
            f"{len(messages)} messages passed to LLM"
        )
    else:
        logger.info("[CONTEXT] History: first turn — no history")

    # ── Build user message ────────────────────────────────────────────────────

    is_first_turn = not history
    user_content  = request.message

    if is_first_turn:
        identity_parts = []
        if request.user_email:
            identity_parts.append(f"Customer email: {request.user_email}")
        if request.order_id:
            identity_parts.append(f"Confirmed order ID: {request.order_id}")
        if identity_parts:
            header = "[" + " | ".join(identity_parts) + "]"
            user_content = f"{header}\n{request.message}"
    else:
        if request.order_id:
            user_content = (
                f"[Confirmed order ID: {request.order_id}]\n"
                f"{request.message}"
            )

    messages.append(Message(role=Role.user, content=user_content))

    # ── Log total estimated input size (now including schema tokens) ──────────
    #
    # Previous estimate only counted message content — that explained why logs
    # showed ~1,700-2,750 tokens but Groq billed 8,000-11,000.
    # The missing cost was:
    #   - Tool schemas (sent every iteration): ~250 tokens × N tools
    #   - max_tokens reservation: groq_max_tokens counted against TPD billing
    # We still can't know the post-pruning schema count here (that happens inside
    # GroqService), but we log the baseline (unpruned) to make the gap visible.

    history_tokens  = sum(max(1, len(m.content) // 4) for m in messages if m.content)
    prompt_tokens   = max(1, len(system_prompt) // 4)
    n_schemas       = len(tools)
    schema_tokens   = n_schemas * _TOKENS_PER_SCHEMA

    logger.info(
        f"[CONTEXT] Estimated input — "
        f"system prompt: ~{prompt_tokens} tokens | "
        f"messages: ~{history_tokens} tokens | "
        f"schemas (unpruned, ×2 iters): ~{schema_tokens * 2} tokens | "
        f"max_tokens reservation: ~{getattr(__import__('backend.core.config', fromlist=['get_settings']).get_settings(), 'groq_max_tokens', 1024)} tokens | "
        f"rough total estimate: ~{prompt_tokens + history_tokens + schema_tokens * 2} tokens"
    )

    logger.info(
        f"Running agent — session={request.session_id} "
        f"email={request.user_email} first_turn={is_first_turn}"
    )

    response = await llm.chat(
        messages      = messages,
        tools         = tools,
        system_prompt = system_prompt,
    )

    logger.info(
        f"Agent done — session={request.session_id} "
        f"tools_called={[t.tool_name for t in response.tool_calls]}"
    )

    return response