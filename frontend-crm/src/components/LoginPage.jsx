// frontend-crm/src/components/LoginPage.jsx
import { useState } from 'react'

export default function LoginPage({ onLogin, loading, error }) {
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')

  const handleSubmit = (e) => {
    e.preventDefault()
    onLogin(email, password)
  }

  return (
    <div style={styles.root}>
      {/* Left panel — brand */}
      <div style={styles.brand}>
        <div style={styles.brandInner}>
          <div style={styles.logo}>
            <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
              <rect width="28" height="28" rx="6" fill="#3b82f6" />
              <path d="M8 20 L14 8 L20 20" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
              <path d="M10.5 15 L17.5 15" stroke="white" strokeWidth="2" strokeLinecap="round"/>
            </svg>
            <span style={styles.logoText}>Leafy</span>
          </div>
          <h1 style={styles.brandTitle}>Operations<br/>Command</h1>
          <p style={styles.brandSub}>
            Admin-only access. All actions are logged and attributed to your account.
          </p>
          <div style={styles.brandStats}>
            {['Request Queue', 'Live Polling', 'Instant Actions'].map(s => (
              <div key={s} style={styles.brandStat}>
                <span style={styles.brandStatDot} />
                {s}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Right panel — form */}
      <div style={styles.formPanel}>
        <form style={styles.form} onSubmit={handleSubmit}>
          <div style={styles.formHeader}>
            <p style={styles.formEyebrow}>ADMIN PORTAL</p>
            <h2 style={styles.formTitle}>Sign in</h2>
          </div>

          {error && (
            <div style={styles.errorBox}>
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{flexShrink:0}}>
                <circle cx="7" cy="7" r="6" stroke="#ef4444" strokeWidth="1.5"/>
                <path d="M7 4v3.5M7 9.5v.5" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
              {error}
            </div>
          )}

          <div style={styles.field}>
            <label style={styles.label}>Email address</label>
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={e => setEmail(e.target.value)}
              style={styles.input}
              placeholder="admin@leafy.store"
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Password</label>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              style={styles.input}
              placeholder="••••••••••"
            />
          </div>

          <button type="submit" disabled={loading} style={styles.btn}>
            {loading ? (
              <span style={styles.spinner} />
            ) : (
              <>
                Sign in
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  )
}

const styles = {
  root: {
    display: 'flex',
    height: '100vh',
    overflow: 'hidden',
    fontFamily: 'var(--font-ui)',
  },
  brand: {
    flex: '0 0 420px',
    background: 'var(--sidebar-bg)',
    display: 'flex',
    alignItems: 'center',
    padding: '48px',
    position: 'relative',
    overflow: 'hidden',
  },
  brandInner: {
    position: 'relative',
    zIndex: 1,
  },
  logo: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    marginBottom: '56px',
  },
  logoText: {
    color: '#e8e8f0',
    fontSize: '18px',
    fontWeight: '700',
    letterSpacing: '-0.02em',
  },
  brandTitle: {
    color: '#e8e8f0',
    fontSize: '40px',
    fontWeight: '800',
    lineHeight: '1.1',
    letterSpacing: '-0.03em',
    marginBottom: '20px',
  },
  brandSub: {
    color: '#4a5068',
    fontSize: '13px',
    lineHeight: '1.7',
    maxWidth: '280px',
    marginBottom: '40px',
  },
  brandStats: {
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
  },
  brandStat: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    color: '#6b7280',
    fontSize: '12px',
    fontWeight: '500',
    letterSpacing: '0.02em',
  },
  brandStatDot: {
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    background: '#3b82f6',
    flexShrink: 0,
  },
  formPanel: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'var(--bg)',
  },
  form: {
    width: '360px',
    display: 'flex',
    flexDirection: 'column',
    gap: '24px',
  },
  formHeader: {
    marginBottom: '8px',
  },
  formEyebrow: {
    fontSize: '11px',
    fontWeight: '600',
    letterSpacing: '0.08em',
    color: 'var(--text-muted)',
    marginBottom: '8px',
  },
  formTitle: {
    fontSize: '28px',
    fontWeight: '700',
    color: 'var(--text-primary)',
    letterSpacing: '-0.02em',
  },
  errorBox: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    padding: '10px 14px',
    background: 'var(--red-bg)',
    border: '1px solid #fca5a5',
    borderRadius: '8px',
    color: 'var(--red-text)',
    fontSize: '12px',
    fontWeight: '500',
  },
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
  label: {
    fontSize: '12px',
    fontWeight: '600',
    color: 'var(--text-secondary)',
    letterSpacing: '0.01em',
  },
  input: {
    padding: '10px 14px',
    border: '1.5px solid var(--border)',
    borderRadius: '8px',
    fontSize: '13px',
    fontFamily: 'var(--font-ui)',
    background: 'var(--surface)',
    color: 'var(--text-primary)',
    outline: 'none',
    transition: 'border-color 0.15s',
  },
  btn: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '8px',
    padding: '12px',
    background: 'var(--text-primary)',
    color: 'var(--bg)',
    border: 'none',
    borderRadius: '8px',
    fontSize: '13px',
    fontWeight: '600',
    cursor: 'pointer',
    transition: 'opacity 0.15s',
    marginTop: '8px',
  },
  spinner: {
    display: 'block',
    width: '16px',
    height: '16px',
    border: '2px solid rgba(255,255,255,0.3)',
    borderTopColor: 'white',
    borderRadius: '50%',
    animation: 'spin 0.7s linear infinite',
  },
}