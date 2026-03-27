import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { Markdown } from '../components/Markdown'

interface SpecDetail {
  id: string
  task_id: string
  content: string
  created_at: string
}

interface SpecDetailResponse {
  spec: SpecDetail
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function SpecDetailPage() {
  const { spec_id } = useParams<{ spec_id: string }>()
  const { data, isLoading, error } = useQuery<SpecDetailResponse>({
    queryKey: ['spec', spec_id],
    queryFn: () => apiFetch<SpecDetailResponse>(`/api/specs/${spec_id}`),
    enabled: !!spec_id,
  })

  if (isLoading) return <div className="loading-state">Loading spec...</div>
  if (error) return <div className="error-state">Failed to load spec</div>
  if (!data) return <div className="error-state">Spec not found</div>

  const { spec } = data

  return (
    <div className="page">
      <header className="page-header">
        <h1>Spec</h1>
        <Link to={`/tasks/${spec.task_id}`} className="btn btn-secondary">
          ← Back to Task
        </Link>
      </header>

      <div className="modal-meta-row">
        <div className="modal-field">
          <div className="modal-label">Created</div>
          <div className="modal-value modal-value-sm">{formatDate(spec.created_at)}</div>
        </div>
        <div className="modal-field">
          <div className="modal-label">Task</div>
          <div className="modal-value modal-value-sm">
            <Link to={`/tasks/${spec.task_id}`} className="link-soft">{spec.task_id}</Link>
          </div>
        </div>
      </div>

      <div className="modal-field">
        <div className="modal-label">Content</div>
        <div className="dark-surface">
          <Markdown content={spec.content} />
        </div>
      </div>
    </div>
  )
}
