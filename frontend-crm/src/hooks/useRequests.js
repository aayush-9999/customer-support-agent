// frontend-crm/src/hooks/useRequests.js
//
// Replaces the 10-second setInterval with a WebSocket connection to /ws/admin.
// When the backend broadcasts a "new_request" event, we reload immediately.
// The CRM never polls blindly anymore — it only fetches when something changed.

import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchRequests, fetchStats, approveRequest, rejectRequest, fetchEscalations, resolveEscalation } from '../api.js' 

export function useRequests(tab = 'pending') {
  const [requests, setRequests]         = useState([])
  const [stats, setStats]               = useState({ pending: 0, approved: 0, rejected: 0, total: 0 })
  const [loading, setLoading]           = useState(true)
  const [error, setError]               = useState(null)
  const [animatingOut, setAnimatingOut] = useState(new Set())

  const wsRef = useRef(null)

  // ── Data fetcher (called on mount + on WS push) ──────────────────────────────
  const load = useCallback(async () => {
    try {
      const [reqs, st] = await Promise.all([fetchRequests(tab), fetchStats()])
      setRequests(Array.isArray(reqs) ? reqs : [])
      setStats(st)
      setError(null)
    } catch (e) {
      if (e.message !== 'Unauthorized') setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [tab])

  // ── Initial load + tab-change refetch ────────────────────────────────────────
  useEffect(() => {
    setLoading(true)
    load()
  }, [load])

  // ── WebSocket — connect once on mount, reconnect on disconnect ───────────────
  useEffect(() => {
    let reconnectTimer = null
    let destroyed = false

    function connect() {
      if (destroyed) return

      // Use wss:// in production, ws:// locally — mirror the current page proto
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${window.location.host}/ws/admin`)
      wsRef.current = ws

      ws.onopen = () => {
        console.log('[CRM WS] connected')
      }

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data)
          if (msg.type === 'new_request') {
            load()
          }
          if (msg.type === 'new_escalation') {
            load()
          }
        } catch {
          // ignore malformed frames
        }
      }

      ws.onclose = () => {
        if (destroyed) return
        console.log('[CRM WS] disconnected — reconnecting in 3s')
        // Reconnect with a short backoff so the CRM self-heals on flaky networks
        reconnectTimer = setTimeout(connect, 3000)
      }

      ws.onerror = (err) => {
        // Let onclose handle reconnect; just log the error
        console.warn('[CRM WS] error', err)
        ws.close()
      }
    }

    connect()

    return () => {
      destroyed = true
      clearTimeout(reconnectTimer)
      if (wsRef.current) {
        wsRef.current.onclose = null   // prevent reconnect loop on intentional unmount
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [load])

  // ── Approve / Reject action ───────────────────────────────────────────────────
  const action = useCallback(async (id, type, note = '') => {
    setAnimatingOut(prev => new Set([...prev, id]))

    try {
      if (type === 'approve') {
        await approveRequest(id)
      } else {
        await rejectRequest(id, note)
      }
    } catch (e) {
      setAnimatingOut(prev => { const s = new Set(prev); s.delete(id); return s })
      setError(e.message || 'Action failed. Please try again.')  // ← NEW: surface it
      throw e
    }

    // Wait for CSS animation, then remove from list and refresh stats
    setTimeout(async () => {
      setRequests(prev => prev.filter(r => r.id !== id))
      setAnimatingOut(prev => { const s = new Set(prev); s.delete(id); return s })
      try {
        const st = await fetchStats()
        setStats(st)
      } catch {}
    }, 350)
  }, [])

  return { requests, stats, loading, error, animatingOut, reload: load, action }
}