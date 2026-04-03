// frontend/src/hooks/useAuth.js

import { useState, useCallback } from "react";
import { login as apiLogin, register as apiRegister, token, closeConversation } from "../api";

export function useAuth() {
  const [user,    setUser]    = useState(() => {
    try {
      const stored = localStorage.getItem("leafy_user");
      return stored ? JSON.parse(stored) : null;
    } catch { return null; }
  });
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);

  const login = useCallback(async (credentials) => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiLogin(credentials);
      token.set(data.access_token);
      localStorage.setItem("leafy_user", JSON.stringify(data.user));
      setUser(data.user);
      return data.user;
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const register = useCallback(async (payload) => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiRegister(payload);
      token.set(data.access_token);
      localStorage.setItem("leafy_user", JSON.stringify(data.user));
      setUser(data.user);
      return data.user;
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async (sessionId) => {
    if (sessionId) {
      await closeConversation(sessionId).catch(() => {});
    }
    token.clear();
    localStorage.removeItem("leafy_user");
    setUser(null);
  }, []);

  return { user, loading, error, login, register, logout, setError };
}