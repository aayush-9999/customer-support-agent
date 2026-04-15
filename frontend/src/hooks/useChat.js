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
      id:             makeId(),
      role:           m.role,
      content:        m.content,
      timestamp:      normaliseTimestamp(m.timestamp),
      isNotification: m.role === "notification",
      status:         m.status || null,
      isError:        false,
    }));
}

/**
 * Ensure an ISO timestamp string is always parsed as UTC by JS.
 * If the string already has +HH:MM or Z — leave it alone.
 * If it's a bare "YYYY-MM-DDTHH:mm:ss[.ffffff]" — append Z.
 */
function normaliseTimestamp(ts) {
  if (!ts) return new Date().toISOString();
  if (typeof ts !== "string") return new Date(ts).toISOString();
  if (ts.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(ts)) return ts;
  return ts + "Z";
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useChat(user) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  /**
   * sessionIdRef is the authoritative "which session owns the in-flight request"
   * guard. We use a ref (not state) because closures in async callbacks need to
   * see the latest value synchronously — state updates are batched and async.
   *
   * NOTE: We do NOT sync this via a useEffect. A useEffect sync introduces a
   * one-render lag: if the user sends a message in the same render cycle that
   * triggered the session change, the ref would still hold the old value. We
   * assign sessionIdRef.current directly at every call site instead.
   */
  const sessionIdRef       = useRef(null);
  const wsRef              = useRef(null);
  const reconnectTimerRef  = useRef(null); // tracks pending WS reconnect timers
  const abortControllerRef = useRef(null);

  // ── WebSocket ─────────────────────────────────────────────────────────────

  /**
   * connectWebSocket(sid)
   *
   * Opens a fresh WS connection for the given session, closing any previous one.
   * Auto-reconnects after 3 s on unexpected close so the user never misses
   * an admin approval/rejection notification mid-session.
   *
   * Guard: if the user switches session while a reconnect is pending, we cancel
   * the timer before opening the new connection, so we never resurrect a stale
   * socket for the old session.
   */
  const connectWebSocket = useCallback((sid) => {
    // Cancel any pending reconnect timer for the previous session
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    // Null out onclose first so the old socket's reconnect path doesn't fire
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }

    // Guard: nothing to connect to
    if (!sid) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;

    const ws = new WebSocket(`${protocol}//${host}/ws/${sid}`);
    // Assign immediately so the wsRef.current !== ws guard in onclose works
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

        // Guard: only inject notification into the session that owns this WS.
        // If the user has already switched to a different chat, sid is stale.
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
      // Log for debugging; let onclose handle reconnect (onerror fires before
      // onclose on most browsers, so we avoid double-reconnect by not acting here)
      console.error("WebSocket error:", err);
      ws.close();
    };

    ws.onclose = () => {
      // Only reconnect if this socket is still the active one for this session.
      // If wsRef.current was already replaced (intentional switch), bail out.
      if (wsRef.current !== ws) return;

      // Only reconnect if we're still in the same session.
      if (sessionIdRef.current !== sid) return;

      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        // Re-check session hasn't changed during the delay
        if (sessionIdRef.current === sid) {
          connectWebSocket(sid);
        }
      }, 3000);
    };
  }, []); // stable — no external deps; reconnect closes over sid

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

  // ── Send a message ─────────────────────────────────────────────────────────

  const send = useCallback(
    async (text) => {
      const sendingSessionId = sessionIdRef.current;
      if (!sendingSessionId || !text.trim()) return;

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      const controller = new AbortController();
      abortControllerRef.current = controller;

      const userMsg = {
        id:        makeId(),
        role:      "user",
        content:   text.trim(),
        timestamp: new Date().toISOString(),
        isError:   false,
      };
      setMessages((prev) => [...prev, userMsg]);
      setLoading(true);

      try {
        const data = await apiSendMessage({
          message:   text.trim(),
          sessionId: sendingSessionId,
          signal:    controller.signal,
        });

        if (sessionIdRef.current !== sendingSessionId) return;

        setMessages((prev) => [
          ...prev,
          {
            id:        makeId(),
            role:      "assistant",
            content:   data.reply,
            // normaliseTimestamp ensures the backend's tz-naive strings are
            // always treated as UTC, consistent with buildMessages()
            timestamp: normaliseTimestamp(data.timestamp) || new Date().toISOString(),
            isError:   false,
          },
        ]);

        // Refresh sidebar history in the background
        getConversationHistory()
          .then((d) => setConversations(d.conversations || []))
          .catch(() => {});

      } catch (err) {
        if (err.name === "AbortError") return;
        if (sessionIdRef.current !== sendingSessionId) return;

        setMessages((prev) => [
          ...prev,
          {
            id:        makeId(),
            role:      "assistant",
            content:   "Something went wrong. Please try again.",
            timestamp: new Date().toISOString(),
            isError:   true,
          },
        ]);
      } finally {
        if (sessionIdRef.current === sendingSessionId) {
          setLoading(false);
        }
      }
    },
    []
  );

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
    } catch (_) {
      // Session creation failed — leave sessionId/ref as-is so the user sees
      // an empty chat rather than a fake local session that would silently drop
      // every message on the backend. The UI can surface the error separately.
      console.error("Failed to create new session");
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
      if (reconnectTimerRef.current)  clearTimeout(reconnectTimerRef.current);
      if (abortControllerRef.current) abortControllerRef.current.abort();
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect loop on unmount
        wsRef.current.close();
      }
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