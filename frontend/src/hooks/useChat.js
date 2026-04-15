import { useState, useEffect, useRef, useCallback } from "react";
import {
  getNewSession,
  sendMessage as apiSendMessage,
  getConversationHistory,
} from "../api";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeId() {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

function makeNotification(content, status) {
  return {
    id: makeId(),
    role: "notification",
    content,
    timestamp: new Date().toISOString(),
    isNotification: true,
    status,
    isError: false,
  };
}

/**
 * Convert raw DB message rows into UI-friendly format
 */
function buildMessages(rawMsgs = []) {
  return rawMsgs
    .filter(
      (m) =>
        (m.role === "user" ||
          m.role === "assistant" ||
          m.role === "notification") &&
        m.content &&
        !m.content.startsWith("__tool_calls__:")
    )
    .map((m) => ({
      id: makeId(),
      role: m.role,
      content: m.content,
      timestamp: m.timestamp || new Date().toISOString(),
      isNotification: m.role === "notification",
      status: m.status || null,
      isError: false,
    }));
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useChat(user) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  const sessionIdRef = useRef(null);
  const abortControllerRef = useRef(null);
  const wsRef = useRef(null);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // ── WebSocket ─────────────────────────────────────────────────────────────

  const connectWebSocket = useCallback((sid) => {
    // Close existing socket
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    if (!sid) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;

    const ws = new WebSocket(`${protocol}//${host}/ws/${sid}`);
    wsRef.current = ws;

    ws.onopen = async () => {
      try {
        const data = await getConversationHistory();
        const currentConv = (data.conversations || []).find(
          (c) => c.session_id === sid
        );

        if (!currentConv) return;

        const dbNotifications = (currentConv.messages || [])
          .filter((m) => m.role === "notification")
          .map((m) => makeNotification(m.content, m.status || "approved"));

        if (dbNotifications.length === 0) return;

        setMessages((prev) => {
          const seen = new Set(
            prev.filter((m) => m.isNotification).map((m) => m.content)
          );
          const fresh = dbNotifications.filter((n) => !seen.has(n.content));
          return fresh.length ? [...prev, ...fresh] : prev;
        });
      } catch (err) {
        console.error("WS onopen sync error:", err);
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type !== "request_resolved") return;
        if (sessionIdRef.current !== sid) return;

        setMessages((prev) => [
          ...prev,
          makeNotification(data.message, data.status),
        ]);
      } catch (err) {
        console.error("WS message parse error:", err);
      }
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      ws.close();
    };

    ws.onclose = () => {
      if (wsRef.current !== ws) return;

      setTimeout(() => {
        if (sessionIdRef.current === sid) {
          connectWebSocket(sid);
        }
      }, 3000);
    };
  }, []);

  // ── Init on login ──────────────────────────────────────────────────────────

  useEffect(() => {
    if (!user) return;

    let cancelled = false;

    async function init() {
      try {
        const data = await getConversationHistory();
        if (!cancelled) {
          setConversations(data.conversations || []);
        }
      } catch (err) {
        console.error("History load error:", err);
      } finally {
        if (!cancelled) setHistoryLoaded(true);
      }

      try {
        const sid = await getNewSession();
        if (cancelled) return;

        sessionIdRef.current = sid;
        setSessionId(sid);
        connectWebSocket(sid);
      } catch (err) {
        console.error("Session creation error:", err);
      }
    }

    init();
    return () => {
      cancelled = true;
    };
  }, [user, connectWebSocket]);

  // ── Send message ───────────────────────────────────────────────────────────

  const send = useCallback(async (text) => {
    const sendingSessionId = sessionIdRef.current;

    if (!sendingSessionId || !text.trim()) return;

    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    const userMsg = {
      id: makeId(),
      role: "user",
      content: text.trim(),
      timestamp: new Date().toISOString(),
      isError: false,
    };

    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const data = await apiSendMessage({
        message: text.trim(),
        sessionId: sendingSessionId,
        signal: controller.signal,
      });

      if (sessionIdRef.current !== sendingSessionId) return;

      setMessages((prev) => [
        ...prev,
        {
          id: makeId(),
          role: "assistant",
          content: data.reply,
          timestamp: data.timestamp || new Date().toISOString(),
          isError: false,
        },
      ]);

      getConversationHistory()
        .then((d) => setConversations(d.conversations || []))
        .catch(() => {});
    } catch (err) {
      if (err.name === "AbortError") return;

      if (sessionIdRef.current !== sendingSessionId) return;

      setMessages((prev) => [
        ...prev,
        {
          id: makeId(),
          role: "assistant",
          content: "Something went wrong. Please try again.",
          timestamp: new Date().toISOString(),
          isError: true,
        },
      ]);
    } finally {
      if (sessionIdRef.current === sendingSessionId) {
        setLoading(false);
      }
    }
  }, []);

  // ── New chat ───────────────────────────────────────────────────────────────

  const newChat = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    setMessages([]);
    setLoading(false);

    try {
      const sid = await getNewSession();
      sessionIdRef.current = sid;
      setSessionId(sid);
      connectWebSocket(sid);
    } catch {
      const sid = crypto.randomUUID();
      sessionIdRef.current = sid;
      setSessionId(sid);
      connectWebSocket(sid);
    }
  }, [connectWebSocket]);

  // ── Load conversation ──────────────────────────────────────────────────────

  const loadConversation = useCallback(
    (conv) => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }

      sessionIdRef.current = conv.session_id;
      setSessionId(conv.session_id);
      setLoading(false);
      setMessages(buildMessages(conv.messages || []));
      connectWebSocket(conv.session_id);
    },
    [connectWebSocket]
  );

  // ── Cleanup ────────────────────────────────────────────────────────────────

  useEffect(() => {
    return () => {
      if (abortControllerRef.current) abortControllerRef.current.abort();
      if (wsRef.current) wsRef.current.close();
    };
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