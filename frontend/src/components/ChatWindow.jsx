// frontend/src/components/ChatWindow.jsx

import { useState, useRef, useEffect } from "react";
import { MessageBubble }   from "./MessageBubble";
import { TypingIndicator } from "./TypingIndicator";

const SUGGESTIONS = [
  "Where is my order?",
  "I'd like to return an item",
  "Change my delivery date",
  "Check my account details",
];

export function ChatWindow({ user, messages, loading, onSend, sessionId }) {
  const [input, setInput] = useState("");
  const bottomRef         = useRef(null);
  const inputRef          = useRef(null);
  const messagesRef       = useRef(null);

  // ── Auto-scroll to bottom on new messages / loading state ─────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // ── Re-focus input and scroll to bottom whenever session changes ───────────
  // This fixes the UX problem where switching chats leaves focus in a void.
  useEffect(() => {
    if (!sessionId) return;
    // Small timeout lets React finish painting the new messages before we scroll
    const t = setTimeout(() => {
      inputRef.current?.focus();
      bottomRef.current?.scrollIntoView({ behavior: "instant" });
    }, 50);
    return () => clearTimeout(t);
  }, [sessionId]);

  // ── Auto-grow textarea height ──────────────────────────────────────────────
  const handleInputChange = (e) => {
    const el = e.target;
    setInput(el.value);
    // Reset then grow to fit content
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  };

  const handleSend = () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    // Reset textarea height after clearing
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }
    onSend(text);
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSuggestion = (s) => {
    if (loading) return;
    onSend(s);
    inputRef.current?.focus();
  };

  const isEmpty = messages.length === 0;

  return (
    <div className="chat-window">

      {/* ── Header ── */}
      <div className="chat-header">
        <div className="chat-header__brand">
          <div className="chat-header__avatar">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
              <path d="M12 2a5 5 0 1 0 0 10A5 5 0 0 0 12 2zM2 20c0-4 4.5-7 10-7s10 3 10 7"/>
            </svg>
            <span className="chat-header__dot" />
          </div>
          <div>
            <p className="chat-header__name">Support Agent</p>
            <p className="chat-header__status">Online &middot; replies instantly</p>
          </div>
        </div>
      </div>

      {/* ── Messages ── */}
      <div className="chat-messages" ref={messagesRef}>

        {isEmpty && (
          <div className="chat-empty">
            <div className="chat-empty__icon">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
            </div>
            <h2 className="chat-empty__title">
              Hi {user?.name || "there"} 👋
            </h2>
            <p className="chat-empty__sub">
              How can I help you today?<br />
              Ask me anything about your orders or account.
            </p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  className="suggestion-chip"
                  onClick={() => handleSuggestion(s)}
                  disabled={loading}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {loading && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>

      {/* ── Input ── */}
      <div className="chat-input-area">
        <div className="input-row">
          <textarea
            ref={inputRef}
            className="chat-input"
            placeholder={loading ? "Waiting for reply…" : "Type a message…"}
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKey}
            rows={1}
            disabled={loading}
          />
          <button
            className={`send-btn ${input.trim() && !loading ? "send-btn--active" : ""}`}
            onClick={handleSend}
            disabled={!input.trim() || loading}
            title="Send message"
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="22" y1="2" x2="11" y2="13"/>
              <polygon points="22 2 15 22 11 13 2 9 22 2"/>
            </svg>
          </button>
        </div>
      </div>

    </div>
  );
}