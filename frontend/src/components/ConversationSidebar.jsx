  // frontend/src/components/ConversationSidebar.jsx

// ── Helpers ───────────────────────────────────────────────────────────────────

function timeAgo(isoString) {
  try {
    const diff  = Date.now() - new Date(isoString).getTime();
    const mins  = Math.floor(diff / 60_000);
    const hours = Math.floor(diff / 3_600_000);
    const days  = Math.floor(diff / 86_400_000);
    if (mins  < 2)  return "Just now";
    if (mins  < 60) return `${mins}m`;
    if (hours < 24) return `${hours}h`;
    if (days  < 7)  return `${days}d`;
    return new Date(isoString).toLocaleDateString([], { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

function getPreview(conv) {
    const msgs = conv.messages || [];
    // Walk backwards to find the last REAL assistant reply.
    // Skip tool-call encoding rows (start with __tool_calls__:) and empty content.
    const last = [...msgs].reverse().find(
      (m) =>
        m.role === "assistant" &&
        m.content &&
        !m.content.startsWith("__tool_calls__:") &&
        m.content.trim().length > 0
    );
    return last?.content?.slice(0, 60) || "No messages yet";
  }

/**
 * Extract a meaningful title from the conversation.
 * Uses the first user message that isn't a system header.
 */
function getTitle(conv) {
  const msgs = conv.messages || [];
  const first = msgs.find(
    (m) =>
      m.role === "user" &&
      m.content &&
      !m.content.startsWith("[Customer email:") &&
      !m.content.startsWith("[Earlier conversation") &&
      m.content.trim().length > 0
  );
  if (!first) return "New conversation";
  // Strip the identity header injected by the backend [Customer email: ... | ...]
  const clean = first.content.replace(/^\[.*?\]\n/, "").trim();
  return clean.length > 46 ? clean.slice(0, 46) + "…" : clean;
}

/**
 * Extract the last assistant reply as a preview line.
 * Skips tool-call encoding rows and empty content.
 */
function getPreview(conv) {
  const msgs = conv.messages || [];
  const last  = [...msgs].reverse().find(
    (m) =>
      m.role === "assistant" &&
      m.content &&
      !m.content.startsWith("__tool_calls__:") &&
      m.content.trim().length > 0
  );
  if (!last) return "No reply yet";
  const text = last.content.replace(/\n/g, " ").trim();
  return text.length > 54 ? text.slice(0, 54) + "…" : text;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ConversationSidebar({
  user,
  conversations,
  historyLoaded,
  onLoadConversation,
  onNewChat,
  onLogout,
  activeSessionId,
}) {
  return (
    <aside className="sidebar">

      {/* ── Agent brand strip ── */}
      <div className="sidebar__brand">
        <div className="sidebar__brand-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        </div>
        <div>
          <p className="sidebar__brand-name">Support Chat</p>
          <p className="sidebar__brand-tag">We&rsquo;re here to help</p>
        </div>
      </div>

      {/* ── User profile ── */}
      <div className="sidebar__profile">
        <div className="sidebar__avatar">
          {user?.name?.[0]?.toUpperCase() || "?"}
        </div>
        <div className="sidebar__user-info">
          <p className="sidebar__user-name">
            {user?.name} {user?.surname}
          </p>
          <p className="sidebar__user-email">{user?.email}</p>
        </div>
        <button className="sidebar__logout" onClick={onLogout} title="Sign out">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
            <polyline points="16 17 21 12 16 7"/>
            <line x1="21" y1="12" x2="9" y2="12"/>
          </svg>
          New chat
        </button>

      {/* ── New chat ── */}
      <button className="sidebar__new-chat" onClick={onNewChat}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <line x1="12" y1="5" x2="12" y2="19"/>
          <line x1="5"  y1="12" x2="19" y2="12"/>
        </svg>
        New conversation
      </button>

      {/* ── Conversation history ── */}
      <div className="sidebar__section-label">Recent</div>

      <div className="sidebar__convos">
        {!historyLoaded && (
          <div className="sidebar__loading">
            <span className="sidebar__spinner" />
          </div>
        )}

        {historyLoaded && conversations.length === 0 && (
          <p className="sidebar__empty">
            No previous conversations.<br />Start a new one above.
          </p>
        )}

        {conversations.map((conv) => (
          <button
            key={conv.session_id}
            className={`sidebar__convo ${
              conv.session_id === activeSessionId ? "sidebar__convo--active" : ""
            }`}
            onClick={() => onLoadConversation(conv)}
          >
            <div className="sidebar__convo-top">
              <span className="sidebar__convo-title">{getTitle(conv)}</span>
              <span className="sidebar__convo-time">{timeAgo(conv.last_active)}</span>
            </div>
            <p className="sidebar__convo-preview">{getPreview(conv)}</p>
          </button>
        ))}
      </div>

    </aside>
  );
}
