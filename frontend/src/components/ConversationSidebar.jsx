// frontend/src/components/ConversationSidebar.jsx

function timeAgo(isoString) {
  try {
    const diff = Date.now() - new Date(isoString).getTime();
    const mins  = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days  = Math.floor(diff / 86400000);
    if (mins  < 2)  return "Just now";
    if (mins  < 60) return `${mins}m ago`;
    if (hours < 24) return `${hours}h ago`;
    return `${days}d ago`;
  } catch { return ""; }
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

const tierColors = {
  Bronze:   { bg: "#fdf2e8", text: "#92470d" },
  Silver:   { bg: "#f1f3f5", text: "#495057" },
  Gold:     { bg: "#fef9e7", text: "#7d6608" },
  Platinum: { bg: "#eaf4fb", text: "#1565c0" },
};

export function ConversationSidebar({ user, conversations, historyLoaded, onLoadConversation, onNewChat, onLogout, activeSessionId }) {

  const tier = user?.loyaltyTier || "Bronze";
  const colors = tierColors[tier] || tierColors.Bronze;

  return (
    <aside className="sidebar">
      {/* User profile */}
      <div className="sidebar__profile">
        <div className="sidebar__avatar">
          {user?.name?.[0]?.toUpperCase() || "?"}
        </div>
        <div className="sidebar__user-info">
          <p className="sidebar__user-name">{user?.name} {user?.surname}</p>
          <p className="sidebar__user-email">{user?.email}</p>
        </div>
        <button className="sidebar__logout" onClick={onLogout} title="Sign out">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
            <polyline points="16 17 21 12 16 7"/>
            <line x1="21" y1="12" x2="9" y2="12"/>
          </svg>
        </button>
      </div>

      {/* Loyalty badge */}
      <div className="sidebar__loyalty" style={{ background: colors.bg, color: colors.text }}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
        </svg>
        <span>{tier} · {user?.loyaltyPoints?.toLocaleString() || 0} pts</span>
      </div>

      {/* New chat button */}
      <button className="sidebar__new-chat" onClick={onNewChat}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
        New chat
      </button>

      {/* Conversation history */}
      <div className="sidebar__section-label">Recent chats</div>

      <div className="sidebar__convos">
        {!historyLoaded && (
          <div className="sidebar__loading">
            <span className="sidebar__spinner" />
          </div>
        )}

        {historyLoaded && conversations.length === 0 && (
          <p className="sidebar__empty">No previous chats</p>
        )}

        {conversations.map((conv) => (
          <button
            key={conv.session_id}
            className={`sidebar__convo ${conv.session_id === activeSessionId ? "sidebar__convo--active" : ""}`}
            onClick={() => onLoadConversation(conv)}
          >
            <div className="sidebar__convo-top">
              <span className="sidebar__convo-count">
                {conv.messages?.length || 0} messages
              </span>
              <span className="sidebar__convo-time">{timeAgo(conv.last_active)}</span>
            </div>
            <p className="sidebar__convo-preview">{getPreview(conv)}</p>
          </button>
        ))}
      </div>
    </aside>
  );
}