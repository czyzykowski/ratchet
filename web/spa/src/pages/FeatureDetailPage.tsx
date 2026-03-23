import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { Markdown } from '../components/Markdown'
import { CreateFeatureChat } from '../components/CreateFeatureChat'
import { FeatureProgressBar } from '../components/FeatureProgressBar'
import { STATUS_COLORS } from '../utils/statusColors'

interface Feature {
  id: string
  project_id: string
  title: string
  description: string
  session_id: string | null
  status: string
  abandoned: boolean
}

interface HighLevelSpec {
  id: string
  feature_id: string
  task_id: string | null
  title: string
  order: number
  content: string
  compiled: boolean
  dependencies: string[]
  task_status: string | null
}

interface FeatureDetailResponse {
  feature: Feature
  specs: HighLevelSpec[]
}

export function FeatureDetailPage() {
  const { feature_id } = useParams<{ feature_id: string }>()
  const queryClient = useQueryClient()
  const [showChat, setShowChat] = useState(false)
  const [chatMode, setChatMode] = useState<'clarification' | 'view'>('clarification')
  const [showAbandonForm, setShowAbandonForm] = useState(false)
  const [abandonReason, setAbandonReason] = useState('')
  const [abandonError, setAbandonError] = useState<string | null>(null)
  const { data, isLoading, error } = useQuery<FeatureDetailResponse>({
    queryKey: ['feature', feature_id],
    queryFn: () => apiFetch<FeatureDetailResponse>(`/api/features/${feature_id}`),
    enabled: !!feature_id,
  })

  if (isLoading) return <div className="loading-state">Loading feature...</div>
  if (error) return <div className="error-state">Failed to load feature</div>
  if (!data) return <div className="error-state">Feature not found</div>

  const { feature, specs } = data
  const sortedSpecs = [...specs].sort((a, b) => a.order - b.order)
  const compiledSpecs = sortedSpecs.filter(s => s.compiled)
  const taskStatuses = compiledSpecs.map(s => s.task_status)
  const compiledCount = compiledSpecs.length
  const totalCount = sortedSpecs.length

  const canAbandon = feature.status === 'idea' || feature.status === 'in_clarification'

  function handleChatClose() {
    setShowChat(false)
    queryClient.invalidateQueries({ queryKey: ['feature', feature_id] })
    queryClient.invalidateQueries({ queryKey: ['features'] })
  }

  async function handleAbandonConfirm() {
    setAbandonError(null)
    try {
      await apiFetch(`/api/features/${feature_id}/abandon`, {
        method: 'POST',
        body: JSON.stringify({ reason: abandonReason || null }),
      })
      setShowAbandonForm(false)
      setAbandonReason('')
      queryClient.invalidateQueries({ queryKey: ['feature', feature_id] })
      queryClient.invalidateQueries({ queryKey: ['features'] })
    } catch (err) {
      setAbandonError(err instanceof Error ? err.message : 'Failed to abandon feature')
    }
  }

  if (showChat) {
    const sessionId = chatMode === 'view' ? feature.session_id ?? undefined : undefined
    const initMsg = chatMode === 'clarification'
      ? `# ${feature.title}${feature.description ? '\n\n' + feature.description : ''}`
      : undefined
    return (
      <CreateFeatureChat
        projectId={feature.project_id}
        sessionId={sessionId}
        initialMessage={initMsg}
        featureId={chatMode === 'clarification' ? feature.id : undefined}
        onClose={handleChatClose}
      />
    )
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>
          {feature.title}
          <span className={`badge badge-${feature.status}`} style={{ marginLeft: '0.75rem', verticalAlign: 'middle' }}>{feature.status.replace(/_/g, ' ')}</span>
        </h1>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          {feature.session_id && (
            <button
              className="btn btn-secondary"
              onClick={() => { setChatMode('view'); setShowChat(true) }}
            >
              View Chat
            </button>
          )}
          {feature.status === 'idea' && (
            <button
              className="btn btn-primary"
              onClick={() => { setChatMode('clarification'); setShowChat(true) }}
            >
              Start Clarification
            </button>
          )}
          {canAbandon && !showAbandonForm && (
            <button
              className="btn btn-danger"
              onClick={() => setShowAbandonForm(true)}
            >
              Abandon
            </button>
          )}
          <Link to="/features" className="btn btn-secondary">← Features</Link>
        </div>
      </header>

      {showAbandonForm && (
        <div className="modal-field" style={{ border: '1px solid #5a1a1a', borderRadius: 4, padding: '1rem', background: '#1a0a0a', marginBottom: '1rem' }}>
          <div className="modal-label" style={{ color: '#f87171', marginBottom: '0.5rem' }}>Abandon Feature</div>
          <textarea
            placeholder="Reason for abandonment (optional)"
            value={abandonReason}
            onChange={e => setAbandonReason(e.target.value)}
            style={{ width: '100%', minHeight: '4rem', background: '#111', color: '#e0e0e0', border: '1px solid #2a2a2a', borderRadius: 4, padding: '0.5rem', boxSizing: 'border-box' }}
          />
          {abandonError && (
            <div style={{ color: '#f87171', fontSize: '0.85rem', marginTop: '0.5rem' }}>{abandonError}</div>
          )}
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
            <button className="btn btn-danger" onClick={handleAbandonConfirm}>Confirm Abandon</button>
            <button className="btn btn-secondary" onClick={() => { setShowAbandonForm(false); setAbandonReason(''); setAbandonError(null) }}>Cancel</button>
          </div>
        </div>
      )}

      {totalCount > 0 && (
        <div className="modal-field">
          <FeatureProgressBar
            compiledCount={compiledCount}
            totalCount={totalCount}
            taskStatuses={taskStatuses}
          />
        </div>
      )}

      {feature.description && (
        <div className="modal-field">
          <div className="modal-label">Description</div>
          <div style={{ border: '1px solid #2a2a2a', borderRadius: 4, padding: '0.75rem', background: '#111' }}>
            <Markdown content={feature.description} />
          </div>
        </div>
      )}

      {sortedSpecs.length > 0 && (
        <div className="modal-field">
          <div className="modal-label">High-Level Specs ({sortedSpecs.length})</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginTop: '0.5rem' }}>
            {sortedSpecs.map(spec => (
              <div
                key={spec.id}
                style={{ border: '1px solid #2a2a2a', borderRadius: 4, padding: '0.75rem', background: '#111' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                  <strong style={{ color: '#e0e0e0' }}>
                    {spec.order}. {spec.title}
                  </strong>
                  <span className={`badge badge-${spec.compiled ? 'deployed' : 'ready_for_spec'}`}>
                    {spec.compiled ? 'compiled' : 'pending'}
                  </span>
                  {spec.task_status && (
                    <span
                      className="status-badge"
                      style={{ backgroundColor: STATUS_COLORS[spec.task_status] ?? '#6b7280' }}
                    >
                      {spec.task_status.replace(/_/g, ' ')}
                    </span>
                  )}
                  {spec.task_id && (
                    <Link to={`/tasks/${spec.task_id}`} style={{ color: '#7eb8f7', fontSize: '0.75rem' }}>
                      view task
                    </Link>
                  )}
                </div>
                {spec.dependencies.length > 0 && (
                  <div style={{ fontSize: '0.75rem', color: '#a0a0a0', marginBottom: '0.5rem' }}>
                    Depends on: {spec.dependencies.join(', ')}
                  </div>
                )}
                <Markdown content={spec.content} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
