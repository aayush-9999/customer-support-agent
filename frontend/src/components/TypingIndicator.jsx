// frontend/src/components/TypingIndicator.jsx

export function TypingIndicator() {
  return (
    <div className="msg-row msg-row--bot">
      <div className="msg-avatar">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M12 2a5 5 0 1 0 0 10A5 5 0 0 0 12 2zM2 20c0-4 4.5-7 10-7s10 3 10 7"/>
        </svg>
      </div>
      <div className="bubble bubble--bot typing-bubble">
        <span className="dot" />
        <span className="dot" />
        <span className="dot" />
      </div>
    </div>
  );
}