// frontend/src/api.js

const BASE_URL = "/api";

// ── Token helpers ─────────────────────────────────────────────────────────────

export const token = {
  get:    ()      => localStorage.getItem("leafy_token"),
  set:    (t)     => localStorage.setItem("leafy_token", t),
  clear:  ()      => localStorage.removeItem("leafy_token"),
};

function authHeaders() {
  const t = token.get();
  return {
    "Content-Type": "application/json",
    ...(t ? { Authorization: `Bearer ${t}` } : {}),
  };
}

async function handleResponse(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function login({ email, password }) {
  return handleResponse(
    await fetch(`${BASE_URL}/auth/login`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ email, password }),
    })
  );
}

export async function register({ name, surname, email, password, phone }) {
  return handleResponse(
    await fetch(`${BASE_URL}/auth/register`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ name, surname, email, password, phone }),
    })
  );
}

// ── Session ───────────────────────────────────────────────────────────────────

export async function getNewSession() {
  const data = await handleResponse(
    await fetch(`${BASE_URL}/session/new`, { headers: authHeaders() })
  );
  return data.session_id;
}

// ── Chat ──────────────────────────────────────────────────────────────────────

export async function sendMessage({ message, sessionId, orderId }) {
  return handleResponse(
    await fetch(`${BASE_URL}/chat`, {
      method:  "POST",
      headers: authHeaders(),
      body:    JSON.stringify({
        message,
        session_id: sessionId,
        order_id:   orderId || null,
      }),
    })
  );
}

// ── Conversations ─────────────────────────────────────────────────────────────

export async function getConversationHistory() {
  return handleResponse(
    await fetch(`${BASE_URL}/conversations`, { headers: authHeaders() })
  );
}

export async function closeConversation(sessionId) {
  return handleResponse(
    await fetch(`${BASE_URL}/conversations/close`, {
      method:  "POST",
      headers: authHeaders(),
      body:    JSON.stringify({ session_id: sessionId }),
    })
  );
}