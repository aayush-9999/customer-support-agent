import { useState }          from 'react'
import { useEscalations }    from '../hooks/useEscalations.js'
import EscalationDrawer      from './EscalationDrawer.jsx'

const TABS = [
  { id: 'open',     label: 'Open' },
  { id: 'resolved', label: 'Resolved' },
]

const REASON_LABELS = {
  delivered_not_received:       'Delivered — Not Received',
  refund_not_received:          'Refund Not Received',
  damage_outside_return_window: 'Damage Outside Return Window',
  account_suspended:            'Account Suspended',
  data_request:                 'Data Request',
  legal_threat:                 'Legal Threat',
  high_value_dispute:           'High Value Dispute',
  repeated_contact:             'Repeated Contact',
  customer_requested:           'Customer Requested',
  other:                        'Other',
}

export default function EscalationsView() {
  const [tab, setTab]             = useState('open')
  const [selected, setSelected]   = useState(null)

  const { escalations, loading, error, animatingOut, resolve } = useEscalations(tab)

  return (
    <div style={styles.root}>

      {/* Header */}
      <div style={styles.header}>
        <div style={styles.headerLeft}>
          <h2 style={styles.title}>Escalations</h2>
          <span style={styles.liveChip}>
            <span style={styles.liveDot} />
            Live
          </span>
        </div>
      </div>

      <div style={styles.body}>

        {/* Table */}
        <div style={styles.tablePanel}>

          {/* Tabs */}
          <div style={styles.tabBar}>
            {TABS.map(t => (
              <button
                key={t.id}
                style={{
                  ...styles.tab,
                  color: tab === t.id ? 'var(--text-primary)' : 'var(--text-muted)',
                  borderBottomColor: tab === t.id ? 'var(--text-primary)' : 'transparent',
                }}
                onClick={() => { setTab(t.id); setSelected(null) }}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* Column headers */}
          <div style={styles.colHeaders}>
            <span style={{ ...styles.col, flex: '0 0 180px' }}>Customer</span>
            <span style={{ ...styles.col, flex: '0 0 200px' }}>Reason</span>
            <span style={{ ...styles.col, flex: 1 }}>Note</span>
            <span style={{ ...styles.col, flex: '0 0 60px' }}>Age</span>
            <span style={{ ...styles.col, flex: '0 0 80px', textAlign: 'right' }}>Priority</span>
          </div>

          {/* Rows */}
          <div style={styles.rows}>
            {loading && escalations.length === 0 && (
              <div style={styles.empty}>Loading escalations…</div>
            )}
            {error && (
              <div style={{ ...styles.empty, color: 'var(--red-text)' }}>{error}</div>
            )}
            {!loading && !error && escalations.length === 0 && (
              <div style={styles.empty}>No {tab} escalations</div>
            )}
            {escalations.map(esc => (
              <EscalationRow
                key={esc._id}
                esc={esc}
                selected={selected?._id === esc._id}
                animatingOut={animatingOut.has(esc._id)}
                onClick={() => setSelected(selected?._id === esc._id ? null : esc)}
                reasonLabels={REASON_LABELS}
              />
            ))}
          </div>
        </div>

        {/* Drawer */}
        {selected && (
          <EscalationDrawer
            escalation={selected}
            onResolve={resolve}
            onClose={() => setSelected(null)}
            reasonLabels={REASON_LABELS}
          />
        )}
      </div>
    </div>
  )
}

function EscalationRow({ esc, selected, animatingOut, onClick, reasonLabels }) {
  const customer = esc.customer || {}
  const tier     = customer.loyaltyTier || 'Bronze'
  const age      = formatAge(esc.created_at)

  const rowStyle = {
    ...styles.row,
    ...(animatingOut ? { animation: 'fadeOut 0.35s forwards', pointerEvents: 'none' } : {}),
  }
  if (selected) {
    rowStyle.background       = '#eef2ff'
    rowStyle.borderLeftColor  = 'var(--blue)'
    rowStyle.borderLeftWidth  = '3px'
  }
  if (esc.priority) {
    rowStyle.borderLeftColor = '#ef4444'
    rowStyle.borderLeftWidth = '3px'
  }

  return (
    <div style={rowStyle} onClick={onClick}>
      <div style={{ ...styles.cell, flex: '0 0 180px' }}>
        <div style={styles.customerName}>{customer.name || '—'}</div>
        <span className={`badge badge-${tier.toLowerCase()}`}>{tier}</span>
      </div>
      <div style={{ ...styles.cell, flex: '0 0 200px' }}>
        <span style={{ fontSize: '12px', fontWeight: '600', color: 'var(--text-primary)' }}>
          {reasonLabels[esc.reason] || esc.reason}
        </span>
      </div>
      <div style={{ ...styles.cell, flex: 1, overflow: 'hidden' }}>
        <span style={{ fontSize: '11.5px', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {esc.customer_note || '—'}
        </span>
      </div>
      <div style={{ ...styles.cell, flex: '0 0 60px' }}>
        <span style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>{age}</span>
      </div>
      <div style={{ ...styles.cell, flex: '0 0 80px', justifyContent: 'flex-end' }}>
        {esc.priority && (
          <span style={styles.priorityBadge}>PRIORITY</span>
        )}
      </div>
    </div>
  )
}
function formatAge(iso) {
  if (!iso) return '—'
  const normalized = iso.endsWith('Z') || iso.includes('+')
    ? iso
    : iso.replace(' ', 'T') + 'Z'
  const diff = Date.now() - new Date(normalized).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1)   return 'just now'
  if (m < 60)  return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24)  return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d`
}
const styles = {
  root: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '14px 20px 12px',
    borderBottom: '1px solid var(--border)',
    background: 'var(--surface)',
    flexShrink: 0,
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
  },
  title: {
    fontSize: '14px',
    fontWeight: '700',
    color: 'var(--text-primary)',
    letterSpacing: '0.01em',
  },
  liveChip: {
    display: 'flex',
    alignItems: 'center',
    gap: '5px',
    fontSize: '10px',
    fontWeight: '600',
    color: 'var(--green)',
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
  },
  liveDot: {
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    background: 'var(--green)',
  },
  body: {
    flex: 1,
    display: 'flex',
    overflow: 'hidden',
  },
  tablePanel: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    borderRight: '1px solid var(--border)',
  },
  tabBar: {
    display: 'flex',
    padding: '0 16px',
    background: 'var(--surface)',
    borderBottom: '1px solid var(--border)',
    flexShrink: 0,
  },
  tab: {
    padding: '11px 14px',
    fontSize: '12px',
    fontWeight: '600',
    background: 'transparent',
    border: 'none',
    borderBottom: '2px solid transparent',
    cursor: 'pointer',
    marginBottom: '-1px',
  },
  colHeaders: {
    display: 'flex',
    alignItems: 'center',
    padding: '0 16px',
    height: '32px',
    background: 'var(--surface-alt)',
    borderBottom: '1px solid var(--border)',
    flexShrink: 0,
  },
  col: {
    fontSize: '10px',
    fontWeight: '700',
    color: 'var(--text-muted)',
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
  },
  rows: {
    flex: 1,
    overflowY: 'auto',
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    padding: '0 16px',
    height: 'var(--row-h)',
    borderBottom: '1px solid var(--border)',
    borderLeft: '3px solid transparent',
    cursor: 'pointer',
    background: 'var(--surface)',
  },
  cell: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    overflow: 'hidden',
  },
  customerName: {
    fontSize: '13px',
    fontWeight: '600',
    color: 'var(--text-primary)',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  priorityBadge: {
    fontSize: '9px',
    fontWeight: '700',
    letterSpacing: '0.06em',
    color: '#ef4444',
    border: '1px solid #ef4444',
    borderRadius: '4px',
    padding: '2px 5px',
  },
  empty: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: '120px',
    color: 'var(--text-muted)',
    fontSize: '13px',
  },
}