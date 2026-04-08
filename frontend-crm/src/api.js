// frontend-crm/src/api.js
// All API calls go through this module.
// The Vite proxy forwards /api/* → http://localhost:8000

const BASE = '/api'

function getToken() {
  return localStorage.getItem('crm_token')
}

async function request(method, path, body = null) {
  const headers = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (res.status === 401 || res.status === 403) {
    // Token expired or role check failed — clear and let app redirect
    localStorage.removeItem('crm_token')
    localStorage.removeItem('crm_user')
    window.dispatchEvent(new Event('crm:unauthorized'))
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }

  return res.json()
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function login(email, password) {
  const data = await request('POST', '/auth/login', { email, password })
  if (data.user?.role !== 'admin') {
    throw new Error('Access denied — admin accounts only')
  }
  return data
}

// ── Admin requests ────────────────────────────────────────────────────────────
function normalizeRequest(req) {
  return {
    ...req,
    _id:             req._id            ?? req.id,             // Mongo: _id  | PG: id
    requested_value: req.requested_value ?? req.requested_date, // Mongo field | PG field
    current_value:   req.current_value   ?? req.current_date,   // Mongo field | PG field
  }
}

export async function fetchRequests(status = 'pending') {
  const data = await request('GET', `/admin/requests?status=${status}`)
  const rows = Array.isArray(data) ? data : (data.requests ?? [])
  return rows.map(normalizeRequest)
}
export async function fetchStats() {
  return request('GET', '/admin/requests/stats')
}

export async function approveRequest(id) {
  return request('POST', `/admin/requests/${id}/approve`)
}

export async function rejectRequest(id, note = '') {
  return request('POST', `/admin/requests/${id}/reject`, { note })
}