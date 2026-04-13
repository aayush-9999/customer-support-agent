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
VERBATIM_TURNS = 3

# Approximate token cost per tool schema sent to the LLM.
# With the 2-meta-tool architecture only 2 schemas are ever sent.
_TOKENS_PER_SCHEMA = 250

SYSTEM_PROMPT_TEMPLATE = """
You are a customer support agent for Leafy, a D2C fashion and lifestyle brand.
You have access to real customer data through tools.

══ REASONING ══
Before calling any tool, call the `think` tool first to plan your approach:
  1. What is the customer asking?
  2. Do I already have the data I need in this conversation? (Check history CAREFULLY)
  3. If yes → use that data to reply. Do NOT re-fetch.
  4. If no → what do I need to find, and do I have all required arguments?
Never call `think` at the same time as a data tool. Think first, then act.
Never call a tool with a guessed or invented argument value.
Only report what a tool actually returned. If it errors, say so.
Do not narrate tool calls — call silently, reply with the result.
Always display order IDs in full — never truncate, shorten, or abbreviate them.
When passing order_id to any tool, always use the exact ID from the tool result — never reconstruct or retype it from memory.

══ STRICT ANTI-HALLUCINATION RULES ══
NEVER tell the customer that a date change, return, or address change was submitted
unless you can see the tool's actual response with outcome "pending_approval" or "updated"
in this conversation.
NEVER claim to have order details unless a data tool actually returned them
with success:true in this conversation. Planned ≠ done.
If your think call said you would call a tool — you have NOT called it yet.
You MUST still call it. Do not reply to the customer until the actual tool has run.

══ APPROVAL WORKFLOW ══
When a delivery date change or return tool returns outcome "pending_approval":
  - Tell the customer their request has been SUBMITTED and is PENDING approval.
  - Tell them they will hear back within 24 hours.
  - Do NOT say the date has been changed or confirmed.
  - Do NOT say the request was approved.
When the tool returns outcome "rejected":
  - Explain the reason clearly and offer the earliest_possible date if provided.
When the tool returns outcome "already_pending":
  - Tell them a request is already under review and they should wait.

══ GREETINGS ══
If the customer's first message is a greeting with no question attached — greet back and ask how you can help. Do NOT call any tool on a pure greeting.

══ ORDER WORKFLOW ══
When a customer asks about "my order" without specifying which one:
  Step 1 → Call think ALONE. Then search for and call the tool that lists all customer orders by email.
  Step 2 → 0 orders: "There are no orders on this account."
            1 order: use it directly.
            2+ orders: list them (item name — date — status), ask which one.
  Step 3 → Wait for the customer to pick one.
  Step 4 → Call think ALONE. Then search for and call the tool that gets full order details by order_id.

IMPORTANT: If order details were already fetched for an order this session,
do NOT re-fetch. The data is in your history — use it.

When a customer wants to change the delivery date:
  - Must have a confirmed order_id before calling the date-change tool.
  - If customer says "sooner" / no specific date: call think ALONE first.
    Then fetch order details if NOT already fetched, read estimated_warehouse_date,
    compute earliest = warehouse_date + 1 day, tell the customer and wait for confirmation.
  - Never ask the customer to supply a date they cannot know.
When a customer wants to cancel an order:
  Step 1 → Call think, then get_order_details(order_id) to check status.
  Step 2 → Based on status:
    processing/Processing
      → Call cancel_order(order_id, user_email, reason="auto").
      → Tell customer: cancelled, refund in 3–5 business days.
    invoiced/Invoiced
      → Tell customer: requires admin review (usually 1 business day).
      → Ask: "Could you share a brief reason for cancelling?"
      → Wait for reply, then call cancel_order(order_id, user_email, reason=<reply>).
      → Confirm request submitted.
    shipped/Shipped/delivered/Delivered
      → Explain: can't cancel once dispatched.
      → Advise: wait for delivery, then initiate return through chat.
    cancelled/Cancelled
      → Confirm already cancelled. Refund in 3–5 business days.
    created/other
      → Explain: can't cancel in current state.
      → Advise: contact support.
  Never call cancel_order without confirmed order_id or with guessed reason.

══ CANNOT DO ══
Cannot: upgrade shipping speed, expedite, waive fees, modify order contents,
promise delivery dates beyond what the data shows.

══ KNOWLEDGE CONTEXT ══
{knowledge_context}

══ TOOL CALLING RULES (STRICT) ══
You have TWO meta-tools. You MUST use them in this sequence:

  STEP A — tool_search(query)
    Call this with a natural-language description of what you need.
    Example: "get all orders for a customer by email"
    Example: "change delivery date for a specific order"
    Example: "initiate return for a delivered order"
    This returns up to 3 matching tools with their exact parameter schemas.

  STEP B — tool_invoke(tool_id, arguments)
    Call this with the exact tool_id from the tool_search result,
    and all required arguments as a JSON object.
    Example: tool_invoke("get_order_history", {"email": "user@example.com"})

RULES:
- ALWAYS call tool_search before tool_invoke in the same reasoning chain.
- NEVER guess or construct a tool_id — only use what tool_search returned.
- NEVER call tool_invoke without a prior tool_search that returned that tool_id.
- If none of the tool_search results fit, search again with different keywords.
- Do NOT call tool_search and tool_invoke at the same time — one step at a time.
- think is the only tool you may call without a prior tool_search.
- Use the structured tool_calls API format only. If a tool is needed, call it directly.
""".strip()


def _build_history_summary(old_messages: list[Message]) -> Message | None:
    """
    Compress old turns into a compact summary message inserted as a system-style
    context block. This is purely rule-based — no LLM call, no latency.

    Extracts:
    - What the customer asked (truncated)
    - What tools were called and key data from results
    - What the agent replied (truncated)

    Note: with meta-tools, tool calls are now tool_search + tool_invoke.
    We extract the _invoked_tool tag from tool_invoke results for the summary,
    so the summary still reads as meaningful data (not just "tool_invoke called").
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
                        name = tc["name"]
                        if name == "think":
                            continue
                        if name == "tool_invoke":
                            # Record the actual invoked tool name, not just "tool_invoke"
                            args = tc.get("arguments", {})
                            real = args.get("tool_id", "tool_invoke")
                            current_turn.setdefault("tools_called", []).append(real)
                        elif name != "tool_search":
                            current_turn.setdefault("tools_called", []).append(name)
                except Exception:
                    pass
            else:
                current_turn["reply"] = msg.content[:150].replace("\n", " ")

        elif msg.role == Role.tool:
            try:
                result = json.loads(msg.content)
                if result.get("success") and result.get("data"):
                    data = result["data"]
                    # Determine the real tool name from _invoked_tool tag if present
                    real_tool_name = result.get("_invoked_tool") or msg.name or ""
                    snippet = _extract_tool_snippet(real_tool_name, data)
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
        elif tool_name == "cancel_order":
            outcome = data.get("outcome", "")
            return f"Cancel order outcome: {outcome} for order {str(data.get('order_id', ''))[-8:]}"
        elif tool_name == "initiate_return":
            return f"Return outcome: {data.get('outcome', '')} request_id={data.get('request_id', '')[:8]}"

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
    system_prompt = SYSTEM_PROMPT_TEMPLATE.replace(
        "{knowledge_context}", knowledge_context
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
    user_content = request.message

    # ALWAYS inject identity header — not just on first turn.
    # Without this, after history compression the model loses the email
    # and guesses a placeholder like customer@example.com on turn 2+.
    identity_parts = []
    if request.user_email:
        identity_parts.append(f"Customer email: {request.user_email}")
    if request.order_id:
        identity_parts.append(f"Confirmed order ID: {request.order_id}")
    if identity_parts:
        header = "[" + " | ".join(identity_parts) + "]"
        user_content = f"{header}\n{request.message}"

    messages.append(Message(role=Role.user, content=user_content))

    # ── Token estimation log ──────────────────────────────────────────────────
    # With meta-tools, only 2 schemas are ever sent regardless of real tool count.
    history_tokens = sum(max(1, len(m.content) // 4) for m in messages if m.content)
    prompt_tokens  = max(1, len(system_prompt) // 4)
    n_schemas      = len(tools)   # always 2 in meta-tool mode
    schema_tokens  = n_schemas * _TOKENS_PER_SCHEMA

    logger.info(
        f"[CONTEXT] Estimated input — "
        f"system prompt: ~{prompt_tokens} tokens | "
        f"messages: ~{history_tokens} tokens | "
        f"schemas ({n_schemas} meta-tools): ~{schema_tokens} tokens | "
        f"rough total: ~{prompt_tokens + history_tokens + schema_tokens} tokens"
    )

    logger.info(
        f"Running agent — session={request.session_id} "
        f"email={request.user_email}"
    )

    response = await llm.chat(
        messages      = messages,
        tools         = tools,
        system_prompt = system_prompt,
        session_id    = request.session_id,
    )

    logger.info(
        f"Agent done — session={request.session_id} "
        f"tools_called={[t.tool_name for t in response.tool_calls]}"
    )

    return response