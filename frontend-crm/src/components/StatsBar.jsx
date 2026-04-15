// frontend-crm/src/components/StatsBar.jsx

export default function StatsBar({ stats, lastUpdated }) {
  const items = [
    { label: 'Pending',  key: 'pending',  color: 'var(--amber)',  bg: 'var(--amber-bg)',  text: 'var(--amber-text)' },
    { label: 'Approved', key: 'approved', color: 'var(--green)',  bg: 'var(--green-bg)',  text: 'var(--green-text)' },
    { label: 'Rejected', key: 'rejected', color: 'var(--red)',    bg: 'var(--red-bg)',    text: 'var(--red-text)' },
    { label: 'Total',    key: 'total',    color: 'var(--blue)',   bg: 'var(--blue-bg)',   text: 'var(--blue-text)' },
  ]

  return (
    <div style={styles.bar}>
      <div style={styles.left}>
        <span style={styles.title}>Request Queue</span>
        {lastUpdated && (
          <span style={styles.updated}>
            <PulseDot />
            Live · updated {formatRelative(lastUpdated)}
          </span>
        )}
      </div>
      <div style={styles.stats}>
        {items.map(({ label, key, color, bg, text }) => (
          <div key={key} style={{ ...styles.stat, background: bg }}>
            <span style={{ ...styles.statNum, color: text, fontFamily: 'var(--font-mono)' }}>
              {(stats[key] ?? 0).toString().padStart(2, '0')}
            </span>
            <span style={{ ...styles.statLabel, color: text }}>{label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function PulseDot() {
  return (
    <span style={{
      display: 'inline-block',
      width: '6px', height: '6px',
      borderRadius: '50%',
      background: 'var(--green)',
      animation: 'pulse-dot 2s ease-in-out infinite',
      flexShrink: 0,
    }} />
  )
}

function formatRelative(date) {
  const normalized = typeof date === 'string'
    ? (date.endsWith('Z') || date.includes('+') ? date : date.replace(' ', 'T') + 'Z')
    : date
  const diff = Math.floor((Date.now() - new Date(normalized)) / 1000)
  if (diff < 5)  return 'just now'
  if (diff < 60) return `${diff}s ago`
  return `${Math.floor(diff / 60)}m ago`
}

const styles = {
  bar: {
    height: 'var(--header-h)',
    borderBottom: '1px solid var(--border)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 20px',
    background: 'var(--surface)',
    flexShrink: 0,
    gap: '16px',
  },
  left: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  title: {
    fontSize: '13px',
    fontWeight: '700',
    color: 'var(--text-primary)',
    letterSpacing: '-0.01em',
  },
  updated: {
    display: 'flex',
    alignItems: 'center',
    gap: '5px',
    fontSize: '11px',
    color: 'var(--text-muted)',
    fontFamily: 'var(--font-mono)',
  },
  stats: {
    display: 'flex',
    gap: '6px',
  },
  stat: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '4px 10px',
    borderRadius: '6px',
  },
  statNum: {
    fontSize: '14px',
    fontWeight: '600',
  },
  statLabel: {
    fontSize: '11px',
    fontWeight: '600',
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
  },
}