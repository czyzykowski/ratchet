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

  function handleChatClose() {
    setShowChat(false)
    queryClient.invalidateQueries({ queryKey: ['feature', feature_id] })
    queryClient.invalidateQueries({ queryKey: ['features'] })
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
          <Link to="/features" className="btn btn-secondary">← Features</Link>
        </div>
      </header>

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
