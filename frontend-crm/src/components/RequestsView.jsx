// frontend-crm/src/components/RequestsView.jsx
import { useState, useEffect } from 'react'
import StatsBar       from './StatsBar.jsx'
import RequestsTable  from './RequestsTable.jsx'
import DetailDrawer   from './DetailDrawer.jsx'
import { useRequests } from '../hooks/useRequests.js'

export default function RequestsView() {
  const [tab, setTab]                   = useState('pending')
  const [selectedRequest, setSelected]  = useState(null)
  const [lastUpdated, setLastUpdated]   = useState(null)

  const { requests, stats, loading, error, animatingOut, reload, action } = useRequests(tab)

  // Track last successful poll time
  useEffect(() => {
    if (!loading) setLastUpdated(new Date())
  }, [requests, loading])

  // Deselect if tab changes
  useEffect(() => { setSelected(null) }, [tab])

  function handleSelect(req) {
    setSelected(req)
  }

  async function handleApprove(id) {
    await action(id, 'approve')
    setSelected(null)
    // Switch to approved tab so admin can see it
    if (tab === 'pending') setTab('pending') // stay on pending to see rest
  }

  async function handleReject(id, note) {
    await action(id, 'reject', note)
    setSelected(null)
  }

  return (
    <div style={styles.root}>
      {/* Stats bar spans full width above the panels */}
      <StatsBar stats={stats} lastUpdated={lastUpdated} />

      {/* Two-panel body */}
      <div style={styles.body}>
        <RequestsTable
          requests={requests}
          loading={loading}
          error={error}
          tab={tab}
          onTabChange={setTab}
          selectedId={selectedRequest?._id}
          onSelect={handleSelect}
          animatingOut={animatingOut}
        />

        {selectedRequest && (
          <DetailDrawer
            request={selectedRequest}
            onApprove={handleApprove}
            onReject={handleReject}
            onClose={() => setSelected(null)}
          />
        )}
      </div>
    </div>
  )
}

const styles = {
  root: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    background: 'var(--bg)',
  },
  body: {
    flex: 1,
    display: 'flex',
    overflow: 'hidden',
  },
}