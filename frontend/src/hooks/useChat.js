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
      timestamp:      m.timestamp || new Date().toISOString(),
      isNotification: m.role === "notification",
      status:         m.status || null,
      isError:        false,
    }));
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
   * tracker. We update it synchronously whenever sessionId changes so that any
   * async callback can check if it is still "current" before touching state.
   *
   * The React state (sessionId) is for rendering; the ref is for closures.
   */
  const sessionIdRef       = useRef(null);
  const abortControllerRef = useRef(null); // AbortController for current fetch
  const wsRef              = useRef(null); // active WebSocket

  // Keep ref in sync with state — runs synchronously after every sessionId change
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // ── WebSocket ─────────────────────────────────────────────────────────────

  const connectWebSocket = useCallback((sid) => {
    // Close any existing socket first
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (!sid) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host     = window.location.host;
    const ws       = new WebSocket(`${protocol}//${host}/ws/${sid}`);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type !== "request_resolved") return;

        // ── GUARD: only inject notification into the session that owns this WS ──
        // If the user has already switched to a different chat, the sid in the
        // closure is stale — do not update the currently visible messages.
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

    ws.onerror = () => {};  // suppress console noise; reconnect not needed for support chat
    ws.onclose = () => {};
    wsRef.current = ws;
  }, []); // stable — no external deps

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
        // Update both state and ref atomically-ish (ref first so callbacks see it)
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
      const sendingSessionId = sessionIdRef.current; // capture NOW, before any await
      if (!sendingSessionId || !text.trim()) return;

      // Abort any previous in-flight request (e.g. user sent while still loading)
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      const controller = new AbortController();
      abortControllerRef.current = controller;

      // Optimistically show the user's message
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
          signal:    controller.signal, // allows us to abort on session switch
        });

        // ── RACE CONDITION GUARD ───────────────────────────────────────────
        // If the user switched to a different chat while we were waiting,
        // sessionIdRef.current is now a different session. Discard this response
        // entirely — do NOT write it into the new chat's message list.
        if (sessionIdRef.current !== sendingSessionId) return;

        setMessages((prev) => [
          ...prev,
          {
            id:        makeId(),
            role:      "assistant",
            content:   data.reply,
            timestamp: data.timestamp || new Date().toISOString(),
            isError:   false,
          },
        ]);

        // Refresh sidebar history in the background — fire and forget
        getConversationHistory()
          .then((d) => setConversations(d.conversations || []))
          .catch(() => {});

      } catch (err) {
        // AbortError means we intentionally cancelled — no UI update
        if (err.name === "AbortError") return;

        // For any other error, only show it if still on the same session
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
        // Only clear the loading spinner if we're still on the same session.
        // If the session changed, newChat/loadConversation already cleared it.
        if (sessionIdRef.current === sendingSessionId) {
          setLoading(false);
        }
      }
    },
    [] // no deps — uses refs for session, so always stable
  );

  // ── New chat ───────────────────────────────────────────────────────────────

  const newChat = useCallback(async () => {
    // Kill any in-flight request for the old session immediately.
    // This prevents the response from arriving later and contaminating state.
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    // Reset UI state first so the user sees a clean chat immediately
    setMessages([]);
    setLoading(false);

    try {
      const sid = await getNewSession();
      sessionIdRef.current = sid; // update ref before state so guards see it first
      setSessionId(sid);
      connectWebSocket(sid);
    } catch (_) {
      // Failed to get a new session — leave on empty state, user can try again
    }
  }, [connectWebSocket]);

  // ── Load existing conversation ─────────────────────────────────────────────

  const loadConversation = useCallback(
    (conv) => {
      // Kill the in-flight request for the old session immediately
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }

      // Update ref BEFORE state so any concurrent callback sees the new session
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
      if (abortControllerRef.current) abortControllerRef.current.abort();
      if (wsRef.current)              wsRef.current.close();
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