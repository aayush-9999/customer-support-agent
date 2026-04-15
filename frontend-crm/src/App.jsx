// frontend-crm/src/App.jsx
import { useAuth }      from './hooks/useAuth.js'
import LoginPage        from './components/LoginPage.jsx'
import NavSidebar       from './components/NavSidebar.jsx'
import RequestsView     from './components/RequestsView.jsx'
import { useState }     from 'react'
import EscalationsView    from './components/EscalationsView.jsx'
export default function App() {
  const { user, loading, error, login, logout } = useAuth()
  const [activeView, setActiveView] = useState('requests')

  // ── Not logged in ─────────────────────────────────────────────────────────
  if (!user) {
    return (
      <LoginPage
        onLogin={login}
        loading={loading}
        error={error}
      />
    )
  }

  // ── Logged in ─────────────────────────────────────────────────────────────
  return (
    <div style={styles.shell}>
      <NavSidebar
        activeView={activeView}
        onViewChange={setActiveView}
        onLogout={logout}
        user={user}
      />

      <main style={styles.main}>
        {activeView === 'requests' && <RequestsView />}
        {activeView === 'escalations' && <EscalationsView />}
      </main>
    </div>
  )
}

const styles = {
  shell: {
    display: 'flex',
    height: '100vh',
    overflow: 'hidden',
    background: 'var(--bg)',
  },
  main: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    minWidth: 0,
  },
}