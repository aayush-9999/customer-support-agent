// frontend/src/components/AuthPage.jsx

import { useState } from "react";

export function AuthPage({ onLogin, onRegister, loading, error, onClearError }) {
  const [tab, setTab] = useState("login"); // "login" | "register"

  // Login state
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPass,  setLoginPass]  = useState("");

  // Register state
  const [regName,    setRegName]    = useState("");
  const [regSurname, setRegSurname] = useState("");
  const [regEmail,   setRegEmail]   = useState("");
  const [regPass,    setRegPass]    = useState("");
  const [regPhone,   setRegPhone]   = useState("");

  const switchTab = (t) => {
    setTab(t);
    onClearError();
  };

  const handleLogin = (e) => {
    e.preventDefault();
    onLogin({ email: loginEmail, password: loginPass });
  };

  const handleRegister = (e) => {
    e.preventDefault();
    onRegister({
      name:     regName,
      surname:  regSurname,
      email:    regEmail,
      password: regPass,
      phone:    regPhone || undefined,
    });
  };

  return (
    <div className="auth-page">
      <div className="auth-card">

        {/* Logo */}
        <div className="auth-logo">
          <div className="auth-logo__icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <span className="auth-logo__name">Support Portal</span>
        </div>

        {/* Tabs */}
        <div className="auth-tabs">
          <button
            className={`auth-tab ${tab === "login" ? "auth-tab--active" : ""}`}
            onClick={() => switchTab("login")}
          >
            Sign in
          </button>
          <button
            className={`auth-tab ${tab === "register" ? "auth-tab--active" : ""}`}
            onClick={() => switchTab("register")}
          >
            Create account
          </button>
        </div>

        {/* Error */}
        {error && (
          <div className="auth-error">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <circle cx="12" cy="12" r="10"/>
              <line x1="12" y1="8" x2="12" y2="12"/>
              <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
            {error}
          </div>
        )}

        {/* Login form */}
        {tab === "login" && (
          <form className="auth-form" onSubmit={handleLogin}>
            <div className="field">
              <label className="field__label">Email</label>
              <input
                className="field__input"
                type="email"
                placeholder="you@example.com"
                value={loginEmail}
                onChange={(e) => setLoginEmail(e.target.value)}
                required
                autoFocus
              />
            </div>
            <div className="field">
              <label className="field__label">Password</label>
              <input
                className="field__input"
                type="password"
                placeholder="••••••••"
                value={loginPass}
                onChange={(e) => setLoginPass(e.target.value)}
                required
              />
            </div>
            <button className="auth-submit" type="submit" disabled={loading}>
              {loading ? <span className="btn-spinner" /> : "Sign in"}
            </button>
          </form>
        )}

        {/* Register form */}
        {tab === "register" && (
          <form className="auth-form" onSubmit={handleRegister}>
            <div className="field-row">
              <div className="field">
                <label className="field__label">First name</label>
                <input
                  className="field__input"
                  type="text"
                  placeholder="Jane"
                  value={regName}
                  onChange={(e) => setRegName(e.target.value)}
                  required
                  autoFocus
                />
              </div>
              <div className="field">
                <label className="field__label">Last name</label>
                <input
                  className="field__input"
                  type="text"
                  placeholder="Doe"
                  value={regSurname}
                  onChange={(e) => setRegSurname(e.target.value)}
                  required
                />
              </div>
            </div>
            <div className="field">
              <label className="field__label">Email</label>
              <input
                className="field__input"
                type="email"
                placeholder="you@example.com"
                value={regEmail}
                onChange={(e) => setRegEmail(e.target.value)}
                required
              />
            </div>
            <div className="field">
              <label className="field__label">
                Password{" "}
                <span className="field__hint">min. 6 characters</span>
              </label>
              <input
                className="field__input"
                type="password"
                placeholder="••••••••"
                value={regPass}
                onChange={(e) => setRegPass(e.target.value)}
                required
                minLength={6}
              />
            </div>
            <div className="field">
              <label className="field__label">
                Phone <span className="field__hint">optional</span>
              </label>
              <input
                className="field__input"
                type="tel"
                placeholder="+1 555 000 0000"
                value={regPhone}
                onChange={(e) => setRegPhone(e.target.value)}
              />
            </div>
            <button className="auth-submit" type="submit" disabled={loading}>
              {loading ? <span className="btn-spinner" /> : "Create account"}
            </button>
            <p className="auth-legal">
              By creating an account you agree to our terms of service.
            </p>
          </form>
        )}

      </div>
    </div>
  );
}