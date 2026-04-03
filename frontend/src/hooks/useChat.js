// frontend/src/hooks/useChat.js

import { useState, useCallback, useRef, useEffect } from "react";
import { sendMessage, getNewSession, getConversationHistory } from "../api";

export function useChat(user) {
  const [messages,       setMessages]       = useState([]);
  const [loading,        setLoading]        = useState(false);
  const [sessionId,      setSessionId]      = useState(null);
  const [conversations,  setConversations]  = useState([]);  // past sessions
  const [historyLoaded,  setHistoryLoaded]  = useState(false);
  const wsRef      = useRef(null);
  const sessionRef = useRef(null);

  // ── Load conversation history on mount ─────────────────────────────────────
  useEffect(() => {
    if (!user) return;
    getConversationHistory()
      .then(data => {
        setConversations(data.conversations || []);
        setHistoryLoaded(true);
      })
      .catch(() => setHistoryLoaded(true));
  }, [user]);

  // ── WebSocket setup ────────────────────────────────────────────────────────
  const openWebSocket = useCallback((sid) => {
    if (wsRef.current) return;

    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws/${sid}`);

    let pingTimer;

    ws.onopen = () => {
      pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 25000);
    };

    ws.onmessage = (event) => {
      if (event.data === "pong") return;
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "request_resolved") {
          setMessages(prev => [...prev, {
            id:             `notify-${Date.now()}`,
            role:           "assistant",
            content:        payload.message,
            isNotification: true,
            status:         payload.status, // "approved" | "rejected"
            timestamp:      new Date().toISOString(),
          }]);
        }
      } catch {}
    };

    ws.onclose = () => {
      clearInterval(pingTimer);
      wsRef.current = null;
    };

    wsRef.current = ws;
  }, []);

  // ── Ensure session exists ──────────────────────────────────────────────────
  const ensureSession = useCallback(async () => {
    if (sessionRef.current) return sessionRef.current;
    const sid = await getNewSession();
    sessionRef.current = sid;
    setSessionId(sid);
    openWebSocket(sid);
    return sid;
  }, [openWebSocket]);

  // ── Send message ───────────────────────────────────────────────────────────
  const send = useCallback(async (text, orderId = null) => {
    if (!text.trim() || loading) return;

    const userMsg = {
      id:        `u-${Date.now()}`,
      role:      "user",
      content:   text,
      timestamp: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);

    try {
      const sid = await ensureSession();
      const response = await sendMessage({ message: text, sessionId: sid, orderId });

      setMessages(prev => [...prev, {
        id:           `a-${Date.now()}`,
        role:         "assistant",
        content:      response.reply,
        wasEscalated: response.was_escalated,
        timestamp:    response.timestamp,
      }]);
    } catch (err) {
      setMessages(prev => [...prev, {
        id:      `e-${Date.now()}`,
        role:    "assistant",
        content: err.message || "Something went wrong. Please try again.",
        isError: true,
      }]);
    } finally {
      setLoading(false);
    }
  }, [loading, ensureSession]);

  // ── Load a past conversation into the current view ─────────────────────────
  const loadConversation = useCallback((conv) => {
    // Close existing WS
    wsRef.current?.close();
    wsRef.current = null;

    // Restore messages from history
    const restored = conv.messages.map((m, i) => ({
      id:        `hist-${i}`,
      role:      m.role,
      content:   m.content,
      timestamp: m.timestamp,
    }));
    setMessages(restored);

    // Reuse the session so the WS can still receive admin notifications
    sessionRef.current = conv.session_id;
    setSessionId(conv.session_id);
    openWebSocket(conv.session_id);
  }, [openWebSocket]);

  // ── Start a fresh session ──────────────────────────────────────────────────
  const newChat = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    sessionRef.current = null;
    setSessionId(null);
    setMessages([]);
  }, []);

  // ── Cleanup on unmount ─────────────────────────────────────────────────────
  useEffect(() => {
    return () => { wsRef.current?.close(); };
  }, []);

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