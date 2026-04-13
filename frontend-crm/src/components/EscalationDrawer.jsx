import { useState } from 'react'

export default function EscalationDrawer({ escalation, onResolve, onClose, reasonLabels }) {
  const [note, setNote]         = useState('')
  const [open, setOpen]         = useState(false)
  const [acting, setActing]     = useState(false)
  const [actionError, setActionError] = useState(null)

  if (!escalation) return null

  const customer = escalation.customer || {}
  const tier     = customer.loyaltyTier || 'Bronze'

  async function handleResolve() {
    setActing(true)
    setActionError(null)
    try {
      await onResolve(escalation._id, note)
      onClose()
    } catch (e) {
      setActionError(e.message)
      setActing(false)
    }
  }

  return (
    <div style={styles.overlay}>
      <div style={styles.drawer}>

        {/* Header */}
        <div style={styles.header}>
          <div>
            <p style={styles.eyebrow}>
              {escalation.priority ? '🔴 Priority Escalation' : 'Escalation'}
            </p>
            <h3 style={styles.title}>
              {reasonLabels[escalation.reason] || escalation.reason}
            </h3>
          </div>
          <button style={styles.closeBtn} onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M3 3l10 10M13 3L3 13" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"/>
            </svg>
          </button>
        </div>

        <div style={styles.body}>

          {/* Customer */}
          <Section title="Customer">
            <Row label="Name"  value={customer.name  || '—'} />
            <Row label="Email" value={<span style={{ fontSize: '11.5px', fontFamily: 'monospace' }}>{customer.email || '—'}</span>} />
            <Row label="Tier"  value={<span className={`badge badge-${tier.toLowerCase()}`}>{tier}</span>} />
          </Section>

          {/* Escalation details */}
          <Section title="Escalation Details">
            <Row label="Reason" value={reasonLabels[escalation.reason] || escalation.reason} />
            {escalation.order_id && (
              <Row label="Order ID" value={
                <span style={{ fontSize: '11.5px', fontFamily: 'monospace' }}>
                  #{escalation.order_id.slice(-8).toUpperCase()}
                </span>
              } />
            )}
            <Row label="Priority" value={
              escalation.priority
                ? <span style={styles.priorityTag}>YES — respond by EOD</span>
                : <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>No</span>
            } />
            <Row label="Submitted" value={
              <span style={{ fontSize: '11.5px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                {formatDateTime(escalation.created_at)}
              </span>
            } />
          </Section>

          {/* Customer note */}
          {escalation.customer_note && (
            <Section title="Customer Note">
              <blockquote style={styles.quote}>
                "{escalation.customer_note}"
              </blockquote>
            </Section>
          )}

          {actionError && (
            <div style={styles.errorBox}>{actionError}</div>
          )}
        </div>

        {/* Footer */}
        {escalation.status === 'open' && (
          <div style={styles.footer}>
            {open ? (
              <div style={styles.form}>
                <p style={styles.formLabel}>Resolution note (optional)</p>
                <textarea
                  style={styles.textarea}
                  rows={3}
                  placeholder="How was this resolved?"
                  value={note}
                  onChange={e => setNote(e.target.value)}
                />
                <div style={styles.formActions}>
                  <button
                    style={styles.cancelBtn}
                    onClick={() => { setOpen(false); setNote('') }}
                    disabled={acting}
                  >
                    Cancel
                  </button>
                  <button
                    style={styles.resolveBtn}
                    onClick={handleResolve}
                    disabled={acting}
                  >
                    {acting ? '…' : 'Confirm resolve'}
                  </button>
                </div>
              </div>
            ) : (
              <button
                style={styles.resolveBtn}
                onClick={() => setOpen(true)}
              >
                ✓ Mark as resolved
              </button>
            )}
          </div>
        )}

        {/* Already resolved */}
        {escalation.status === 'resolved' && escalation.resolved_at && (
          <div style={styles.resolvedFooter}>
            <span style={{ fontSize: '12px', fontWeight: '600', color: 'var(--text-secondary)' }}>
              ✓ Resolved
            </span>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
              {formatDateTime(escalation.resolved_at)}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: '20px' }}>
      <p style={{ fontSize: '10px', fontWeight: '700', letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '8px', paddingBottom: '6px', borderBottom: '1px solid var(--border)' }}>
        {title}
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {children}
      </div>
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', minHeight: '20px' }}>
      <span style={{ fontSize: '11.5px', color: 'var(--text-muted)', fontWeight: '500', flexShrink: 0, paddingTop: '1px', minWidth: '100px' }}>
        {label}
      </span>
      <span style={{ fontSize: '12px', color: 'var(--text-primary)', fontWeight: '500', textAlign: 'right', wordBreak: 'break-word' }}>
        {value}
      </span>
    </div>
  )
}

function formatDateTime(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-GB', {
      day: '2-digit', month: 'short', year: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch { return '—' }
}

const styles = {
  overlay: {
    width: 'var(--drawer-w)',
    height: '100%',
    display: 'flex',
    flexShrink: 0,
  },
  drawer: {
    width: '100%',
    height: '100%',
    background: 'var(--surface)',
    display: 'flex',
    flexDirection: 'column',
    borderLeft: '1px solid var(--border)',
    animation: 'slideInRight 0.25s var(--ease-out)',
  },
  header: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    padding: '16px 18px 14px',
    borderBottom: '1px solid var(--border)',
    flexShrink: 0,
  },
  eyebrow: {
    fontSize: '10px',
    fontWeight: '700',
    letterSpacing: '0.07em',
    textTransform: 'uppercase',
    color: 'var(--text-muted)',
    marginBottom: '4px',
  },
  title: {
    fontSize: '15px',
    fontWeight: '700',
    color: 'var(--text-primary)',
  },
  closeBtn: {
    width: '28px',
    height: '28px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: '6px',
    background: 'var(--surface-alt)',
    color: 'var(--text-muted)',
    border: 'none',
    cursor: 'pointer',
  },
  body: {
    flex: 1,
    overflowY: 'auto',
    padding: '16px 18px',
  },
  quote: {
    padding: '10px 12px',
    background: 'var(--surface-alt)',
    borderLeft: '3px solid var(--border-strong)',
    borderRadius: '0 6px 6px 0',
    fontSize: '12px',
    color: 'var(--text-secondary)',
    lineHeight: '1.6',
    fontStyle: 'italic',
  },
  priorityTag: {
    fontSize: '11px',
    fontWeight: '700',
    color: '#ef4444',
  },
  errorBox: {
    padding: '10px 12px',
    background: 'var(--red-bg)',
    border: '1px solid #fca5a5',
    borderRadius: '6px',
    color: 'var(--red-text)',
    fontSize: '12px',
    marginTop: '12px',
  },
  footer: {
    padding: '14px 18px',
    borderTop: '1px solid var(--border)',
    flexShrink: 0,
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },
  formLabel: {
    fontSize: '11px',
    fontWeight: '600',
    color: 'var(--text-secondary)',
  },
  textarea: {
    padding: '8px 10px',
    border: '1.5px solid var(--border)',
    borderRadius: '6px',
    fontSize: '12px',
    color: 'var(--text-primary)',
    background: 'var(--surface)',
    resize: 'none',
    outline: 'none',
    lineHeight: '1.5',
    fontFamily: 'var(--font-ui)',
  },
  formActions: {
    display: 'flex',
    gap: '8px',
  },
  cancelBtn: {
    flex: 1,
    padding: '9px',
    background: 'var(--surface-alt)',
    color: 'var(--text-secondary)',
    border: '1.5px solid var(--border)',
    borderRadius: '6px',
    fontSize: '12px',
    fontWeight: '600',
    cursor: 'pointer',
    fontFamily: 'var(--font-ui)',
  },
  resolveBtn: {
    width: '100%',
    padding: '11px 16px',
    background: 'var(--green)',
    color: 'white',
    border: 'none',
    borderRadius: '8px',
    fontSize: '13px',
    fontWeight: '700',
    cursor: 'pointer',
    fontFamily: 'var(--font-ui)',
  },
  resolvedFooter: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 18px',
    borderTop: '1px solid var(--border)',
    background: 'var(--surface-alt)',
    flexShrink: 0,
  },
}