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
      // Load sidebar history
      try {
        const data = await getConversationHistory();
        if (!cancelled) {
          setConversations(data.conversations || []);
        }
      } catch { /* sidebar fails silently */ }

      if (!cancelled) setHistoryLoaded(true);

      // Start fresh session
      try {
        // getNewSession() already returns the session_id string (see api.js)
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

    // KEY FIX: role="notification" rows → banner pills, not bubbles.
    // Any unknown/legacy role is silently skipped.
    const restored = (conv.messages || []).flatMap((m) => {
      if (m.role === "notification") {
        return [makeNotification(m.content, m.status || "approved")];
      }
      if (m.role === "user" || m.role === "assistant") {
        return [makeBubble(m.role, m.content, { timestamp: m.timestamp })];
      }
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
  // api.js sendMessage signature: sendMessage({ message, sessionId })
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