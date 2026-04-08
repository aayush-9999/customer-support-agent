// frontend/src/hooks/useChat.js

import { useState, useEffect, useRef, useCallback } from "react";
import {
  sendMessage,
  getConversationHistory,
  getNewSession,
  closeConversation,
} from "../api.js";

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeBubble(role, content, extra = {}) {
  return {
    id:        crypto.randomUUID(),
    role,
    content,
    timestamp: new Date().toISOString(),
    ...extra,
  };
}

function makeNotification(content, status) {
  return {
    id:             crypto.randomUUID(),
    isNotification: true,
    status,            // "approved" | "rejected"
    content,
    timestamp:      new Date().toISOString(),
  };
}

function buildWsUrl(sessionId) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/${sessionId}`;
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useChat(user) {
  const [messages, setMessages]           = useState([]);
  const [loading, setLoading]             = useState(false);
  const [sessionId, setSessionId]         = useState(null);
  const [conversations, setConversations] = useState([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  const wsRef = useRef(null);

  // ── WebSocket connect ──────────────────────────────────────────────────────
  const connectWs = useCallback((sid) => {
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
    }

    const ws = new WebSocket(buildWsUrl(sid));
    wsRef.current = ws;

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "request_resolved") {
          setMessages((prev) => [
            ...prev,
            makeNotification(msg.message, msg.status),
          ]);
        }
      } catch { /* ignore malformed frames */ }
    };

    ws.onerror = () => ws.close();
  }, []);

  // ── Init on login ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!user) {
      setMessages([]);
      setSessionId(null);
      setConversations([]);
      setHistoryLoaded(false);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
      return;
    }

    let cancelled = false;

    async function init() {
      try {
        const data = await getConversationHistory();
        if (!cancelled) {
          setConversations(data.conversations || []);
        }
      } catch { /* sidebar fails silently */ }

      if (!cancelled) setHistoryLoaded(true);

      try {
        const sid = await getNewSession();
        if (!cancelled) {
          setSessionId(sid);
          connectWs(sid);
        }
      } catch {
        if (!cancelled) {
          const sid = crypto.randomUUID();
          setSessionId(sid);
          connectWs(sid);
        }
      }
    }

    init();
    return () => { cancelled = true; };
  }, [user, connectWs]);

  // ── Load a past conversation from the sidebar ──────────────────────────────
  const loadConversation = useCallback((conv) => {
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
    }

    // Restore messages for display, applying these filters:
    //
    //   role="notification"  → banner pill (not a chat bubble)
    //   role="user"          → user bubble ✓
    //   role="assistant"     → only if content is NOT a raw tool-call encoding.
    //                          Messages starting with "__tool_calls__:" are internal
    //                          LLM protocol rows — they are NOT human-readable replies
    //                          and should never be rendered as chat bubbles.
    //   role="tool"          → raw JSON tool result — skip entirely from UI
    //   anything else        → skip silently
    //
    const restored = (conv.messages || []).flatMap((m) => {
      if (m.role === "notification") {
        return [makeNotification(m.content, m.status || "approved")];
      }

      if (m.role === "user") {
        return [makeBubble("user", m.content, { timestamp: m.timestamp })];
      }

      if (m.role === "assistant") {
        // Skip tool-call encoding rows — they start with the internal marker
        if (m.content && m.content.startsWith("__tool_calls__:")) {
          return [];
        }
        // Skip empty content (shouldn't happen, but guard anyway)
        if (!m.content || !m.content.trim()) {
          return [];
        }
        return [makeBubble("assistant", m.content, { timestamp: m.timestamp })];
      }

      // role="tool" (raw JSON result) and any unknown roles are skipped
      return [];
    });

    setMessages(restored);
    setSessionId(conv.session_id);
    connectWs(conv.session_id);
  }, [connectWs]);

  // ── Start a new blank chat ─────────────────────────────────────────────────
  const newChat = useCallback(async () => {
    setMessages([]);
    setLoading(false);
    try {
      const sid = await getNewSession();
      setSessionId(sid);
      connectWs(sid);
    } catch {
      const sid = crypto.randomUUID();
      setSessionId(sid);
      connectWs(sid);
    }
  }, [connectWs]);

  // ── Send a message ─────────────────────────────────────────────────────────
  const send = useCallback(async (text) => {
    if (!text.trim() || loading || !sessionId) return;

    setMessages((prev) => [...prev, makeBubble("user", text)]);
    setLoading(true);

    try {
      const data = await sendMessage({ message: text, sessionId });
      setMessages((prev) => [
        ...prev,
        makeBubble("assistant", data.reply, {
          wasEscalated: data.was_escalated,
        }),
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        makeBubble("assistant", "Something went wrong. Please try again.", {
          isError: true,
        }),
      ]);
    } finally {
      setLoading(false);
    }
  }, [loading, sessionId]);

  return {
    messages,
    loading,
    sessionId,
    conversations,
    historyLoaded,
    send,
    newChat,
    loadConversation,
  };
}