// frontend/src/hooks/useChat.js

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

/**
 * Convert raw DB message rows (from conversation history) into the flat shape
 * that MessageBubble expects. Strips internal tool-call encoding rows.
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
      // ── FIX: normalise timestamp to always be a UTC-flagged ISO string ────
      // MongoDB returns datetimes without a Z suffix when tz_aware is not set.
      // Now that we fixed the backend (tz_aware=True), timestamps will arrive as
      // "2026-04-13T12:33:21+00:00". But we add the fallback for safety: if the
      // string has no offset info, we append Z so JS always treats it as UTC.
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
  // Already has tz info?
  if (ts.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(ts)) return ts;
  // Bare UTC string from old backend — stamp it as UTC
  return ts + "Z";
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useChat(user) {
  const [messages,      setMessages]      = useState([]);
  const [loading,       setLoading]       = useState(false);
  const [sessionId,     setSessionId]     = useState(null);
  const [conversations, setConversations] = useState([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  /**
   * sessionIdRef is the authoritative "which session owns the in-flight request"
   * guard. We use a ref (not state) because closures in async callbacks need to
   * see the latest value synchronously — state updates are batched and async.
   */
  const sessionIdRef       = useRef(null);
  const wsRef              = useRef(null);
  const reconnectTimerRef  = useRef(null);   // for WS auto-reconnect
  const abortControllerRef = useRef(null);

  // ── WebSocket ─────────────────────────────────────────────────────────────

  /**
   * connectWebSocket(sid)
   *
   * Opens a fresh WS connection for the given session, closing any previous one.
   * Auto-reconnects after 3 s on unexpected close so the customer never misses
   * an admin approval/rejection notification mid-session.
   *
   * Guard: if the user switches session while a reconnect is pending, we cancel
   * the timer before opening the new connection, so we never resurrect a stale
   * socket for the old session.
   */
  const connectWebSocket = useCallback((sid) => {
    // ── Cancel any pending reconnect timer for the previous session ──────────
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    // ── Close the previous WS without triggering its reconnect logic ─────────
    // We null out onclose first so the old socket's "reconnect" path doesn't fire.
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host     = window.location.host;
    const ws       = new WebSocket(`${protocol}//${host}/ws/${sid}`);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type !== "request_resolved") return;

        // ── GUARD: only inject notification into the session that owns this WS ──
        // If the user has already switched to a different chat, sid is stale.
        if (sessionIdRef.current !== sid) return;

        setMessages((prev) => [
          ...prev,
          {
            id:             makeId(),
            role:           "notification",
            content:        data.message,
            timestamp:      new Date().toISOString(),
            isNotification: true,
            status:         data.status, // "approved" | "rejected"
            isError:        false,
          },
        ]);
      } catch (_) {
        // Malformed WS frame — ignore
      }
    };

    ws.onerror = () => {
      // Let onclose handle reconnect; onerror fires before onclose on most browsers
      ws.close();
    };

    ws.onclose = () => {
      // Only reconnect if this socket is still the active one for this session.
      // If wsRef.current was already replaced (intentional switch), bail out.
      if (wsRef.current !== ws) return;

      // Only reconnect if we're still in the same session.
      if (sessionIdRef.current !== sid) return;

      // Reconnect after 3 s — same pattern as the admin CRM
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        // Re-check session hasn't changed during the delay
        if (sessionIdRef.current === sid) {
          connectWebSocket(sid);
        }
      }, 3000);
    };

    wsRef.current = ws;
  }, []); // stable — no external deps; reconnect via closure over sid

  // ── Init: bootstrap session + load history ────────────────────────────────

  useEffect(() => {
    if (!user) return;

    let cancelled = false;

    async function init() {
      // 1. Load conversation history in the background
      try {
        const data = await getConversationHistory();
        if (cancelled) return;
        setConversations(data.conversations || []);
      } catch (_) {
        // History load failure is non-fatal
      } finally {
        if (!cancelled) setHistoryLoaded(true);
      }

      // 2. Start a fresh session
      try {
        const sid = await getNewSession();
        if (cancelled) return;
        sessionIdRef.current = sid;
        setSessionId(sid);
        connectWebSocket(sid);
      } catch (_) {
        // Session creation failure — UI will show empty state, user can retry
      }
    }

    init();
    return () => { cancelled = true; };
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
            // normalise so it's always treated as UTC
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
      // Failed to get a new session
    }
  }, [connectWebSocket]);

  // ── Load existing conversation ─────────────────────────────────────────────

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

  // ── Cleanup on unmount ─────────────────────────────────────────────────────

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