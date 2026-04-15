// frontend/src/components/MessageBubble.jsx

function formatTime(ts) {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleTimeString("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit"
    });
  } catch {
    return "";
  }
}

function stripLeakedToolJson(text) {
  if (!text) return "";
  return text
    .replace(/\[\s*\{\s*"name"\s*:\s*"tool_\w+"[\s\S]*?\}\s*\]/g, "")
    .trim();
}

export function MessageBubble({ message }) {
  const isUser = message.role === "user";

  if (message.isNotification) {
    const isApproved = message.status === "approved";
    return (
      <div className="notification-banner">
        <div className={`notification-banner__inner ${isApproved ? "notification-banner--approved" : "notification-banner--rejected"}`}>
          <span className="notification-banner__icon">
            {isApproved ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            )}
          </span>
          <span className="notification-banner__text">{message.content}</span>
        </div>
      </div>
    );
  }

  return (
    <div className={`msg-row ${isUser ? "msg-row--user" : "msg-row--bot"}`}>
      {!isUser && (
        <div className="msg-avatar">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M12 2a5 5 0 1 0 0 10A5 5 0 0 0 12 2zM2 20c0-4 4.5-7 10-7s10 3 10 7"/>
          </svg>
        </div>
      )}

      <div className="msg-content">
        <div className={`bubble ${isUser ? "bubble--user" : "bubble--bot"} ${message.isError ? "bubble--error" : ""}`}>
          <p className="bubble__text">{stripLeakedToolJson(message.content)}</p>
        </div>

        <div className={`msg-meta ${isUser ? "msg-meta--right" : ""}`}>
          {message.wasEscalated && (
            <span className="escalation-tag">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
              Escalated to team
            </span>
          )}
          <span className="msg-time">{formatTime(message.timestamp)}</span>
        </div>
      </div>
    </div>
  );
}