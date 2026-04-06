// frontend-crm/src/hooks/useRequests.js
import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchRequests, fetchStats, approveRequest, rejectRequest } from '../api.js'

const POLL_INTERVAL = 10_000 // 10 seconds

export function useRequests(tab = 'pending') {
  const [requests, setRequests]       = useState([])
  const [stats, setStats]             = useState({ pending: 0, approved: 0, rejected: 0, total: 0 })
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState(null)
  const [animatingOut, setAnimatingOut] = useState(new Set())
  const intervalRef = useRef(null)

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

  useEffect(() => {
    setLoading(true)
    load()
    intervalRef.current = setInterval(load, POLL_INTERVAL)
    return () => clearInterval(intervalRef.current)
  }, [load])

  const action = useCallback(async (id, type, note = '') => {
    // Animate the row out first
    setAnimatingOut(prev => new Set([...prev, id]))

    try {
      if (type === 'approve') {
        await approveRequest(id)
      } else {
        await rejectRequest(id, note)
      }
    } catch (e) {
      // Re-add if it failed
      setAnimatingOut(prev => { const s = new Set(prev); s.delete(id); return s })
      throw e
    }

    // Wait for animation, then remove from list and refresh stats
    setTimeout(async () => {
      setRequests(prev => prev.filter(r => r._id !== id))
      setAnimatingOut(prev => { const s = new Set(prev); s.delete(id); return s })
      try {
        const st = await fetchStats()
        setStats(st)
      } catch {}
    }, 350)
  }, [])

  return { requests, stats, loading, error, animatingOut, reload: load, action }
}