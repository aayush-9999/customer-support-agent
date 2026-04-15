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
  const clean = first.content.replace(/^\[.*?\]\n/, "").trim();
  return clean.length > 46 ? clean.slice(0, 46) + "…" : clean;
}

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

// ── Icons ─────────────────────────────────────────────────────────────────────

function SunIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="5"/>
      <line x1="12" y1="1" x2="12" y2="3"/>
      <line x1="12" y1="21" x2="12" y2="23"/>
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
      <line x1="1" y1="12" x2="3" y2="12"/>
      <line x1="21" y1="12" x2="23" y2="12"/>
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    </svg>
  );
}

function ChevronLeftIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
      <polyline points="15 18 9 12 15 6"/>
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
      <polyline points="9 18 15 12 9 6"/>
    </svg>
  );
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
  collapsed,
  onToggleCollapse,
  theme,
  onToggleTheme,
}) {
  return (
    <aside className={`sidebar ${collapsed ? "sidebar--collapsed" : ""}`}>

      {/* ── Header strip with collapse toggle ── */}
      <div className="sidebar__header">
        {!collapsed && (
          <div className="sidebar__brand-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
        )}
        {!collapsed && (
          <div className="sidebar__brand-text">
            <p className="sidebar__brand-name">Support Chat</p>
            <p className="sidebar__brand-tag">We&rsquo;re here to help</p>
          </div>
        )}
        <button
          className="sidebar__collapse-btn"
          onClick={onToggleCollapse}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <ChevronRightIcon /> : <ChevronLeftIcon />}
        </button>
      </div>

      {/* ── User profile ── */}
      <div className={`sidebar__profile ${collapsed ? "sidebar__profile--collapsed" : ""}`}>
        <div className="sidebar__avatar" title={collapsed ? `${user?.name} ${user?.surname}` : undefined}>
          {user?.name?.[0]?.toUpperCase() || "?"}
        </div>
        {!collapsed && (
          <>
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
            </button>
          </>
        )}
      </div>

      {/* ── Theme toggle ── */}
      <button
        className="sidebar__theme-toggle"
        onClick={onToggleTheme}
        title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      >
        {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        {!collapsed && (
          <span className="sidebar__theme-label">
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </span>
        )}
      </button>

      {/* ── New chat ── */}
      {!collapsed ? (
        <button className="sidebar__new-chat" onClick={onNewChat}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="12" y1="5" x2="12" y2="19"/>
            <line x1="5"  y1="12" x2="19" y2="12"/>
          </svg>
          New conversation
        </button>
      ) : (
        <button
          className="sidebar__new-chat sidebar__new-chat--icon"
          onClick={onNewChat}
          title="New conversation"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="12" y1="5" x2="12" y2="19"/>
            <line x1="5"  y1="12" x2="19" y2="12"/>
          </svg>
        </button>
      )}

      {/* ── Conversation history (hidden when collapsed) ── */}
      {!collapsed && (
        <>
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
        </>
      )}

      {/* ── Logout icon (collapsed only) ── */}
      {collapsed && (
        <button
          className="sidebar__logout-icon"
          onClick={onLogout}
          title="Sign out"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
            <polyline points="16 17 21 12 16 7"/>
            <line x1="21" y1="12" x2="9" y2="12"/>
          </svg>
        </button>
      )}

    </aside>
  );
}
