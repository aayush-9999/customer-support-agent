import { useState, useEffect, useCallback } from 'react'
import { fetchEscalations, resolveEscalation } from '../api.js'

export function useEscalations(status = 'open') {
  const [escalations, setEscalations]   = useState([])
  const [loading, setLoading]           = useState(true)
  const [error, setError]               = useState(null)
  const [animatingOut, setAnimatingOut] = useState(new Set())

  const load = useCallback(async () => {
    try {
      const data = await fetchEscalations(status)
      setEscalations(Array.isArray(data) ? data : [])
      setError(null)
    } catch (e) {
      if (e.message !== 'Unauthorized') setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [status])

  useEffect(() => {
    setLoading(true)
    load()
  }, [load])

  const resolve = useCallback(async (id, note = '') => {
    setAnimatingOut(prev => new Set([...prev, id]))
    try {
      await resolveEscalation(id, note)
    } catch (e) {
      setAnimatingOut(prev => { const s = new Set(prev); s.delete(id); return s })
      throw e
    }
    setTimeout(() => {
      setEscalations(prev => prev.filter(e => e._id !== id))
      setAnimatingOut(prev => { const s = new Set(prev); s.delete(id); return s })
    }, 350)
  }, [])

  return { escalations, loading, error, animatingOut, reload: load, resolve }
}