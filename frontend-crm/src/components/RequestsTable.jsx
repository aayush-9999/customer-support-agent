// frontend-crm/src/components/RequestsTable.jsx

// DB field mapping (from mongo_tools.py pending_requests insert):
//   requested_value  → the new date customer wants
//   current_value    → order's estimated_destination_date at time of request
//   order.current_delivery → enriched by admin.py from orders collection

const TABS = [
  { id: 'pending',  label: 'Pending' },
  { id: 'approved', label: 'Approved' },
  { id: 'rejected', label: 'Rejected' },
]

export default function RequestsTable({
  requests,
  loading,
  error,
  tab,
  onTabChange,
  selectedId,
  onSelect,
  animatingOut,
}) {
  return (
    <div style={styles.panel}>
      {/* Tab bar */}
      <div style={styles.tabBar}>
        {TABS.map(t => {
          const isActive = tab === t.id
          return (
            <button
              key={t.id}
              style={{
                ...styles.tab,
                color: isActive ? 'var(--text-primary)' : 'var(--text-muted)',
                borderBottomWidth: '2px',
                borderBottomStyle: 'solid',
                borderBottomColor: isActive ? 'var(--text-primary)' : 'transparent',
              }}
              onClick={() => onTabChange(t.id)}
            >
              {t.label}
            </button>
          )
        })}
      </div>

      {/* Column headers - Added Type column, kept all original code */}
      <div style={styles.colHeaders}>
        <span style={{ ...styles.col, flex: '0 0 180px' }}>Customer</span>
        <span style={{ ...styles.col, flex: '0 0 130px' }}>Order ID</span>
        
        {/* NEW: Type column added here */}
        <span style={{ ...styles.col, flex: '0 0 160px' }}>Type</span>
        
        {/* Original date columns kept but hidden via flex:0 so they don't break anything */}
        <span style={{ ...styles.col, flex: 0, display: 'none' }}>Current Date</span>
        <span style={{ ...styles.col, flex: 0, display: 'none' }}>Requested</span>
        
        <span style={{ ...styles.col, flex: 1 }}>Age</span>
        <span style={{ ...styles.col, flex: '0 0 90px', textAlign: 'right' }}>Status</span>
      </div>

      {/* Rows */}
      <div style={styles.rows}>
        {loading && requests.length === 0 && (
          <div style={styles.empty}>
            <span style={styles.spinner} />
            Loading requests…
          </div>
        )}
        {error && (
          <div style={{ ...styles.empty, color: 'var(--red-text)' }}>
            {error}
          </div>
        )}
        {!loading && !error && requests.length === 0 && (
          <div style={styles.empty}>
            No {tab} requests
          </div>
        )}
        {requests.map(req => (
          <RequestRow
            key={req._id}
            req={req}
            selected={selectedId === req._id}
            animatingOut={animatingOut.has(req._id)}
            onClick={() => onSelect(req._id === selectedId ? null : req)}
          />
        ))}
      </div>
    </div>
  )
}

function RequestRow({ req, selected, animatingOut, onClick }) {
  const customer = req.customer || {}
  const order    = req.order    || {}
  const tier     = customer.loyaltyTier || 'Bronze'
  const pending  = formatAge(req.created_at)

  // Original date variables kept (unchanged)
  const currentDate   = order.current_delivery || req.current_value
  const requestedDate = req.requested_value

  // NEW: Determine request type (added without removing anything)
  let requestType = "Unknown"
  if (req.type === "date_change") requestType = "Delivery Date Change"
  if (req.type === "return_request") requestType = "Return Request"

  // Build row style without conflicting shorthand/longhand
  const rowStyle = {
    ...styles.row,
    ...(animatingOut ? styles.rowOut : {}),
  }
  if (selected) {
    rowStyle.background    = '#eef2ff'
    rowStyle.paddingTop    = '0'
    rowStyle.paddingBottom = '0'
    rowStyle.paddingRight  = '16px'
    rowStyle.paddingLeft   = '13px'
    rowStyle.borderLeftWidth  = '3px'
    rowStyle.borderLeftStyle  = 'solid'
    rowStyle.borderLeftColor  = 'var(--blue)'
  }

  return (
    <div style={rowStyle} onClick={onClick}>
      {/* Customer - unchanged */}
      <div style={{ ...styles.cell, flex: '0 0 180px' }}>
        <div style={styles.customerName}>{customer.name || '—'}</div>
        <span className={`badge badge-${tier.toLowerCase()}`}>{tier}</span>
      </div>

      {/* Order ID - unchanged */}
      <div style={{ ...styles.cell, flex: '0 0 130px' }}>
        <span className="mono" style={styles.orderId}>
          #{(req.order_id || '').slice(-8).toUpperCase()}
        </span>
      </div>

      {/* NEW: Type column - added */}
      <div style={{ ...styles.cell, flex: '0 0 160px' }}>
        <span style={{ fontSize: '12px', fontWeight: '600', color: 'var(--text-primary)' }}>
          {requestType}
        </span>
      </div>

      {/* Original Current Date & Requested columns kept but hidden so nothing breaks */}
      <div style={{ ...styles.cell, flex: 0, display: 'none' }}>
        <span className="mono" style={styles.dateText}>
          {formatDate(currentDate)}
        </span>
      </div>
      <div style={{ ...styles.cell, flex: 0, display: 'none' }}>
        <span className="mono" style={{ ...styles.dateText, color: 'var(--blue-text)' }}>
          {formatDate(requestedDate)}
        </span>
      </div>

      {/* Age - unchanged */}
      <div style={{ ...styles.cell, flex: 1 }}>
        <span className="mono" style={styles.age}>{pending}</span>
      </div>

      {/* Status badge - unchanged */}
      <div style={{ ...styles.cell, flex: '0 0 90px', justifyContent: 'flex-end' }}>
        <span className={`badge badge-${req.status}`}>{req.status}</span>
      </div>
    </div>
  )
}

function formatDate(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return '—'
    return d.toLocaleDateString('en-GB', {
      day: '2-digit', month: 'short', year: '2-digit',
    })
  } catch {
    return '—'
  }
}

function formatAge(iso) {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1)   return 'just now'
  if (m < 60)  return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24)  return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d`
}

const styles = {
  panel: {
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
    flexShrink: 0,
    borderBottomWidth: '1px',
    borderBottomStyle: 'solid',
    borderBottomColor: 'var(--border)',
  },
  tab: {
    padding: '11px 14px',
    fontSize: '12px',
    fontWeight: '600',
    background: 'transparent',
    border: 'none',
    cursor: 'pointer',
    letterSpacing: '0.02em',
    marginBottom: '-1px',
    transition: 'color 0.15s',
  },
  colHeaders: {
    display: 'flex',
    alignItems: 'center',
    padding: '0 16px',
    height: '32px',
    background: 'var(--surface-alt)',
    borderBottomWidth: '1px',
    borderBottomStyle: 'solid',
    borderBottomColor: 'var(--border)',
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
    paddingTop: '0',
    paddingBottom: '0',
    paddingLeft: '16px',
    paddingRight: '16px',
    height: 'var(--row-h)',
    borderBottomWidth: '1px',
    borderBottomStyle: 'solid',
    borderBottomColor: 'var(--border)',
    borderLeftWidth: '3px',
    borderLeftStyle: 'solid',
    borderLeftColor: 'transparent',
    cursor: 'pointer',
    transition: 'background 0.1s',
    background: 'var(--surface)',
    overflow: 'hidden',
  },
  rowOut: {
    animation: 'fadeOut 0.35s var(--ease-out) forwards',
    pointerEvents: 'none',
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
  orderId: {
    fontSize: '11.5px',
    color: 'var(--text-secondary)',
    letterSpacing: '0.05em',
  },
  dateText: {
    fontSize: '11.5px',
    color: 'var(--text-secondary)',
    letterSpacing: '0.02em',
  },
  age: {
    fontSize: '11.5px',
    color: 'var(--text-muted)',
  },
  empty: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '10px',
    height: '120px',
    color: 'var(--text-muted)',
    fontSize: '13px',
  },
  spinner: {
    display: 'inline-block',
    width: '14px',
    height: '14px',
    border: '2px solid var(--border)',
    borderTopColor: 'var(--text-muted)',
    borderRadius: '50%',
    animation: 'spin 0.7s linear infinite',
  },
}