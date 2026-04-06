// frontend-crm/src/hooks/useAuth.js
import { useState, useEffect, useCallback } from 'react'
import { login as apiLogin } from '../api.js'

export function useAuth() {
  const [user, setUser]       = useState(() => {
    try { return JSON.parse(localStorage.getItem('crm_user')) } catch { return null }
  })
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  // Listen for 401/403 events thrown by the api module
  useEffect(() => {
    const handler = () => { setUser(null) }
    window.addEventListener('crm:unauthorized', handler)
    return () => window.removeEventListener('crm:unauthorized', handler)
  }, [])

  const login = useCallback(async (email, password) => {
    setLoading(true)
    setError(null)
    try {
      const data = await apiLogin(email, password)
      localStorage.setItem('crm_token', data.access_token)
      localStorage.setItem('crm_user', JSON.stringify(data.user))
      setUser(data.user)
      return true
    } catch (e) {
      setError(e.message)
      return false
    } finally {
      setLoading(false)
    }
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem('crm_token')
    localStorage.removeItem('crm_user')
    setUser(null)
  }, [])

  return { user, loading, error, login, logout }
}