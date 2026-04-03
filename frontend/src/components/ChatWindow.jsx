// frontend/src/components/ChatWindow.jsx

import { useState, useRef, useEffect } from "react";
import { MessageBubble }   from "./MessageBubble";
import { TypingIndicator } from "./TypingIndicator";

const SUGGESTIONS = [
  "Where is my order?",
  "What's your return policy?",
  "I want to change my delivery date",
  "Check my loyalty points",
];

export function ChatWindow({ user, messages, loading, onSend, sessionId }) {
  const [input,  setInput]  = useState("");
  const [orderId, setOrderId] = useState("");
  const [showOrderInput, setShowOrderInput] = useState(false);
  const bottomRef = useRef(null);
  const inputRef  = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSend = () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    onSend(text, orderId.trim() || null);
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSuggestion = (s) => {
    onSend(s, null);
  };

  return (
    <div className="chat-window">
      {/* Header */}
      <div className="chat-header">
        <div className="chat-header__brand">
          <div className="chat-header__avatar">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            <span className="chat-header__dot" />
          </div>
          <div>
            <p className="chat-header__name">Leafy Support</p>
            <p className="chat-header__status">Online · responds instantly</p>
          </div>
        </div>

        <div className="chat-header__actions">
          <button
            className={`icon-btn ${showOrderInput ? "icon-btn--active" : ""}`}
            onClick={() => setShowOrderInput(v => !v)}
            title="Set order ID context"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/>
              <circle cx="12" cy="10" r="3"/>
            </svg>
          </button>
        </div>
      </div>

      {/* Order ID context bar */}
      {showOrderInput && (
        <div className="order-bar">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/>
            <circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>
          </svg>
          <input
            className="order-bar__input"
            type="text"
            placeholder="Paste order ID to set context..."
            value={orderId}
            onChange={e => setOrderId(e.target.value)}
          />
          {orderId && (
            <button className="order-bar__clear" onClick={() => setOrderId("")}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          )}
        </div>
      )}

      {/* Messages */}
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <div className="chat-empty__icon">🌿</div>
            <h2 className="chat-empty__title">
              Hi {user?.name || "there"}!
            </h2>
            <p className="chat-empty__sub">How can I help you today?</p>
            <div className="suggestions">
              {SUGGESTIONS.map(s => (
                <button key={s} className="suggestion-chip" onClick={() => handleSuggestion(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map(msg => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {loading && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="chat-input-area">
        {orderId && (
          <div className="input-context-tag">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <rect x="1" y="3" width="15" height="13"/>
            </svg>
            {orderId.slice(-8)}
          </div>
        )}
        <div className="input-row">
          <textarea
            ref={inputRef}
            className="chat-input"
            placeholder="Type a message..."
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            rows={1}
            disabled={loading}
          />
          <button
            className={`send-btn ${input.trim() && !loading ? "send-btn--active" : ""}`}
            onClick={handleSend}
            disabled={!input.trim() || loading}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="22" y1="2" x2="11" y2="13"/>
              <polygon points="22 2 15 22 11 13 2 9 22 2"/>
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}