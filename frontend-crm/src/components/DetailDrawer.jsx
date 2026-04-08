// frontend-crm/src/components/DetailDrawer.jsx
import { useState } from 'react'

export default function DetailDrawer({ request, onApprove, onReject, onClose }) {
  const [rejectOpen, setRejectOpen] = useState(false)
  const [note, setNote]             = useState('')
  const [acting, setActing]         = useState(false)
  const [actionError, setActionError] = useState(null)

  if (!request) return null

  const customer = request.customer || {}
  const order    = request.order    || {}
  const tier     = customer.loyaltyTier || 'Bronze'

  // Detect request type
  const isDateChange = request.type === "date_change"
  const isReturn     = request.type === "return_request"
  const isOrderChange = request.type === "item_change"

  // Existing variables for date change (kept unchanged)
  const currentDate   = order.current_delivery || request.current_value
  const requestedDate = request.requested_value

  const currentVariant = {
  size: request.old_size || '',
  color: request.old_color || '',
  }

  const requestedVariant = {
    size: request.new_size || '',
    color: request.new_color || '',
  }

  async function handleApprove() {
    setActing(true)
    setActionError(null)
    try {
      await onApprove(request._id)
      onClose()
    } catch (e) {
      setActionError(e.message)
      setActing(false)
    }
  }

  async function handleReject() {
    setActing(true)
    setActionError(null)
    try {
      await onReject(request._id, note)
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
            <p style={styles.headerEyebrow}>
              {isDateChange ? "Delivery date change" : 
              isReturn ? "Return Request" : 
              isOrderChange ? "Item Change Request" :
              "Request"}
            </p>
            <h3 style={styles.headerTitle}>
              <span className="mono">#{(request.order_id || '').slice(-8).toUpperCase()}</span>
            </h3>
          </div>
          <button style={styles.closeBtn} onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M3 3l10 10M13 3L3 13" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"/>
            </svg>
          </button>
        </div>

        <div style={styles.body}>

          {/* Customer info - unchanged */}
          <Section title="Customer">
            <Row label="Name"  value={customer.name || '—'} />
            <Row label="Email" value={<span className="mono" style={{fontSize:'11.5px'}}>{customer.email || '—'}</span>} />
            <Row label="Tier"  value={<span className={`badge badge-${tier.toLowerCase()}`}>{tier}</span>} />
          </Section>

          {/* ==================== DATE CHANGE SECTION (Original - untouched) ==================== */}
          {isDateChange && (
            <Section title="Request details">
              <Row label="Current delivery" value={
                <span className="mono" style={styles.dateMono}>
                  {formatDate(currentDate)}
                </span>
              } />
              <Row label="Requested date" value={
                <span className="mono" style={{ ...styles.dateMono, color: 'var(--blue-text)' }}>
                  {formatDate(requestedDate)}
                </span>
              } />
              <Row label="Submitted" value={
                <span className="mono" style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                  {formatDateTime(request.created_at)}
                </span>
              } />
              <Row label="Status" value={
                <span className={`badge badge-${request.status}`}>{request.status}</span>
              } />
            </Section>
          )}

          {/* ==================== NEW: RETURN REQUEST SECTION ==================== */}
          {isReturn && (
            <Section title="Return Request Details">
              <Row label="Reason" value={
                request.reason ? request.reason.replace(/_/g, " ") : '—'
              } />
              <Row label="Items to Return" value={
                Array.isArray(request.items) && request.items.length > 0
                  ? request.items.join(", ")
                  : '—'
              } />
              <Row label="Refund Method" value={
                request.refund_method ? request.refund_method.replace(/_/g, " ") : '—'
              } />
              <Row label="Return Shipping" value={
                request.return_shipping_covered_by === "leafy" 
                  ? "Covered by Leafy" 
                  : "Paid by Customer"
              } />
              <Row label="Submitted" value={
                <span className="mono" style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                  {formatDateTime(request.created_at)}
                </span>
              } />
              <Row label="Status" value={
                <span className={`badge badge-${request.status}`}>{request.status}</span>
              } />
            </Section>
          )}

          {/* ==================== NEW: ORDER CHANGE SECTION ==================== */}
          {isOrderChange && (
            <Section title="Item Change Details">

              <Row label="Item" value={
                request.item_name || '—'
              } />

              <Row label="Current Variant" value={
                currentVariant.size || currentVariant.color
                  ? `${currentVariant.size || '-'} / ${currentVariant.color || '-'}`
                  : '—'
              } />

              <Row label="Requested Variant" value={
                requestedVariant.size || requestedVariant.color
                  ? (
                    <span style={{ color: 'var(--blue-text)', fontWeight: '600' }}>
                      {requestedVariant.size || '-'} / {requestedVariant.color || '-'}
                    </span>
                  )
                  : '—'
              } />

              <Row label="Stock Source" value={
                request.stock_source === "warehouse"
                  ? "Warehouse"
                  : request.stock_source === "products"
                    ? "Product Catalogue"
                    : '—'
              } />

              <Row label="Submitted" value={
                <span className="mono" style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                  {formatDateTime(request.created_at)}
                </span>
              } />

              <Row label="Status" value={
                <span className={`badge badge-${request.status}`}>{request.status}</span>
              } />

            </Section>
          )}

          {/* Order snapshot - kept for both types */}
          {order.products && order.products.length > 0 && (
            <Section title="Order snapshot">
              <Row label="Status" value={
                <span style={styles.orderStatus}>{order.status || '—'}</span>
              } />
              <Row label="Items" value={
                <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                  {order.products.join(', ')}
                </span>
              } />
              {order.address && (
                <Row label="Ship to" value={
                  <span style={{ fontSize: '11.5px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    {formatAddress(order.address)}
                  </span>
                } />
              )}
            </Section>
          )}

          {/* Customer note from agent (if any) - unchanged */}
          {request.customer_note && (
            <Section title="Customer message">
              <blockquote style={styles.quote}>
                "{request.customer_note}"
              </blockquote>
            </Section>
          )}

          {actionError && (
            <div style={styles.errorBox}>{actionError}</div>
          )}
        </div>

        {/* Action footer — only show for pending - unchanged */}
        {request.status === 'pending' && (
          <div style={styles.footer}>
            {rejectOpen ? (
              <div style={styles.rejectForm}>
                <p style={styles.rejectLabel}>Rejection note (optional)</p>
                <textarea
                  style={styles.textarea}
                  rows={3}
                  placeholder="Reason for rejection…"
                  value={note}
                  onChange={e => setNote(e.target.value)}
                />
                <div style={styles.rejectActions}>
                  <button
                    style={styles.cancelBtn}
                    onClick={() => { setRejectOpen(false); setNote('') }}
                    disabled={acting}
                  >
                    Cancel
                  </button>
                  <button
                    style={styles.confirmRejectBtn}
                    onClick={handleReject}
                    disabled={acting}
                  >
                    {acting ? <Spinner light /> : 'Confirm rejection'}
                  </button>
                </div>
              </div>
            ) : (
              <div style={styles.actionRow}>
                <button
                  style={styles.rejectBtn}
                  onClick={() => setRejectOpen(true)}
                  disabled={acting}
                >
                  Reject
                </button>
                <button
                  style={styles.approveBtn}
                  onClick={handleApprove}
                  disabled={acting}
                >
                  {acting ? <Spinner light /> : (
                    <>
                      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
                        <path d="M2.5 7.5L6 11l6.5-7" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                      Approve
                    </>
                  )}
                </button>
              </div>
            )}
          </div>
        )}

        {/* Resolution info if already actioned - unchanged */}
        {request.status !== 'pending' && request.resolved_at && (
          <div style={styles.resolvedFooter}>
            <span style={styles.resolvedLabel}>
              {request.status === 'approved' ? '✓ Approved' : '✕ Rejected'}
            </span>
            <span className="mono" style={styles.resolvedTime}>
              {formatDateTime(request.resolved_at)}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

/* ==================== Helper Components & Styles (unchanged) ==================== */

function Section({ title, children }) {
  return (
    <div style={sectionStyles.root}>
      <p style={sectionStyles.title}>{title}</p>
      <div style={sectionStyles.body}>{children}</div>
    </div>
  )
}

const sectionStyles = {
  root: { marginBottom: '20px' },
  title: {
    fontSize: '10px',
    fontWeight: '700',
    letterSpacing: '0.07em',
    textTransform: 'uppercase',
    color: 'var(--text-muted)',
    marginBottom: '8px',
    paddingBottom: '6px',
    borderBottom: '1px solid var(--border)',
  },
  body: { display: 'flex', flexDirection: 'column', gap: '6px' },
}

function Row({ label, value }) {
  return (
    <div style={rowStyles.root}>
      <span style={rowStyles.label}>{label}</span>
      <span style={rowStyles.value}>{value}</span>
    </div>
  )
}

const rowStyles = {
  root: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: '12px',
    minHeight: '20px',
  },
  label: {
    fontSize: '11.5px',
    color: 'var(--text-muted)',
    fontWeight: '500',
    flexShrink: 0,
    paddingTop: '1px',
    minWidth: '100px',
  },
  value: {
    fontSize: '12px',
    color: 'var(--text-primary)',
    fontWeight: '500',
    textAlign: 'right',
    wordBreak: 'break-word',
  },
}

function Spinner() {
  return (
    <span style={{
      display: 'inline-block',
      width: '14px', height: '14px',
      border: '2px solid rgba(255,255,255,0.35)',
      borderTopColor: 'white',
      borderRadius: '50%',
      animation: 'spin 0.7s linear infinite',
    }} />
  )
}

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('en-GB', {
      weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
    })
  } catch { return '—' }
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

function formatAddress(addr) {
  if (!addr || typeof addr !== 'object') return String(addr || '—')
  return [addr.street, addr.city, addr.country].filter(Boolean).join(', ')
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
    animation: 'slideInRight 0.25s var(--ease-out)',
    borderLeft: '1px solid var(--border)',
  },
  header: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    padding: '16px 18px 14px',
    borderBottom: '1px solid var(--border)',
    background: 'var(--surface)',
    flexShrink: 0,
  },
  headerEyebrow: {
    fontSize: '10px',
    fontWeight: '700',
    letterSpacing: '0.07em',
    textTransform: 'uppercase',
    color: 'var(--text-muted)',
    marginBottom: '4px',
  },
  headerTitle: {
    fontSize: '16px',
    fontWeight: '700',
    color: 'var(--text-primary)',
    letterSpacing: '0.03em',
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
    flexShrink: 0,
  },
  body: {
    flex: 1,
    overflowY: 'auto',
    padding: '16px 18px',
  },
  dateMono: {
    fontSize: '12px',
    fontWeight: '500',
    color: 'var(--text-primary)',
  },
  orderStatus: {
    fontSize: '12px',
    fontWeight: '600',
    color: 'var(--text-secondary)',
    textTransform: 'capitalize',
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
    background: 'var(--surface)',
    flexShrink: 0,
  },
  actionRow: {
    display: 'flex',
    gap: '10px',
  },
  approveBtn: {
    flex: 2,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '6px',
    padding: '11px 16px',
    background: 'var(--green)',
    color: 'white',
    border: 'none',
    borderRadius: '8px',
    fontSize: '13px',
    fontWeight: '700',
    cursor: 'pointer',
    fontFamily: 'var(--font-ui)',
    transition: 'opacity 0.15s',
    letterSpacing: '0.01em',
  },
  rejectBtn: {
    flex: 1,
    padding: '11px 16px',
    background: 'transparent',
    color: 'var(--red)',
    border: '2px solid var(--red)',
    borderRadius: '8px',
    fontSize: '13px',
    fontWeight: '700',
    cursor: 'pointer',
    fontFamily: 'var(--font-ui)',
    transition: 'background 0.15s, color 0.15s',
    letterSpacing: '0.01em',
  },
  rejectForm: {
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },
  rejectLabel: {
    fontSize: '11px',
    fontWeight: '600',
    color: 'var(--text-secondary)',
    letterSpacing: '0.02em',
  },
  textarea: {
    padding: '8px 10px',
    border: '1.5px solid var(--border)',
    borderRadius: '6px',
    fontSize: '12px',
    fontFamily: 'var(--font-ui)',
    color: 'var(--text-primary)',
    background: 'var(--surface)',
    resize: 'none',
    outline: 'none',
    lineHeight: '1.5',
  },
  rejectActions: {
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
  confirmRejectBtn: {
    flex: 2,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '9px',
    background: 'var(--red)',
    color: 'white',
    border: 'none',
    borderRadius: '6px',
    fontSize: '12px',
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
  resolvedLabel: {
    fontSize: '12px',
    fontWeight: '600',
    color: 'var(--text-secondary)',
  },
  resolvedTime: {
    fontSize: '11px',
    color: 'var(--text-muted)',
  },
}