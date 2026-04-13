// frontend-crm/src/components/NavSidebar.jsx

export default function NavSidebar({ activeView, onViewChange, onLogout, user }) {
  const navItems = [
    {
      id: 'requests',
      label: 'Request Queue',
      icon: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <rect x="2" y="2" width="14" height="2.5" rx="1.25" fill="currentColor" opacity=".4"/>
          <rect x="2" y="7.75" width="14" height="2.5" rx="1.25" fill="currentColor"/>
          <rect x="2" y="13.5" width="9" height="2.5" rx="1.25" fill="currentColor" opacity=".4"/>
        </svg>
      ),
    },
    {
      id: 'escalations',
      label: 'Escalations',
      icon: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path d="M9 2L9 10" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"/>
          <circle cx="9" cy="14" r="1.5" fill="currentColor"/>
          <path d="M3 16 L9 3 L15 16" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
        </svg>
      ),
    },
  ]

  return (
    <nav style={styles.nav}>
      {/* Logo mark */}
      <div style={styles.logoMark}>
        <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
          <rect width="22" height="22" rx="5" fill="#3b82f6" />
          <path d="M6 17 L11 6 L16 17" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
          <path d="M8 13 L14 13" stroke="white" strokeWidth="1.75" strokeLinecap="round"/>
        </svg>
      </div>

      {/* Nav items */}
      <div style={styles.items}>
        {navItems.map(item => (
          <button
            key={item.id}
            title={item.label}
            style={{
              ...styles.navBtn,
              ...(activeView === item.id ? styles.navBtnActive : {}),
            }}
            onClick={() => onViewChange(item.id)}
          >
            {item.icon}
            {activeView === item.id && <span style={styles.activeDot} />}
          </button>
        ))}
      </div>

      {/* Bottom — user avatar + logout */}
      <div style={styles.bottom}>
        <button
          title="Sign out"
          style={styles.navBtn}
          onClick={onLogout}
        >
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <path d="M7 9h8M12 6l3 3-3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            <path d="M10 3H4a1 1 0 00-1 1v10a1 1 0 001 1h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </button>
        <div style={styles.avatar} title={user?.email}>
          {(user?.name || 'A')[0].toUpperCase()}
        </div>
      </div>
    </nav>
  )
}

const styles = {
  nav: {
    width: 'var(--sidebar-w)',
    height: '100%',
    background: 'var(--sidebar-bg)',
    borderRight: '1px solid var(--sidebar-border)',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    padding: '14px 0',
    flexShrink: 0,
    zIndex: 10,
  },
  logoMark: {
    marginBottom: '24px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  items: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
    width: '100%',
    alignItems: 'center',
    paddingTop: '4px',
  },
  navBtn: {
    position: 'relative',
    width: '36px',
    height: '36px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: '8px',
    color: 'var(--sidebar-icon)',
    background: 'transparent',
    border: 'none',
    cursor: 'pointer',
    transition: 'color 0.15s, background 0.15s',
  },
  navBtnActive: {
    color: 'var(--sidebar-icon-active)',
    background: '#1e2130',
  },
  activeDot: {
    position: 'absolute',
    right: '-1px',
    top: '50%',
    transform: 'translateY(-50%)',
    width: '3px',
    height: '16px',
    background: 'var(--sidebar-dot)',
    borderRadius: '2px',
  },
  bottom: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '8px',
  },
  avatar: {
    width: '28px',
    height: '28px',
    borderRadius: '50%',
    background: '#1e2130',
    color: '#6b7280',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '11px',
    fontWeight: '700',
    letterSpacing: '0.05em',
    border: '1px solid #2a2f45',
  },
}