import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { Markdown } from '../components/Markdown'

interface ExecutionDetail {
  id: string
  task_id: string
  spec_id: string
  branch_name: string | null
  started_at: string
  status: string
  completed_at: string | null
  failure_reason: string | null
}

interface ExecutionDetailResponse {
  execution: ExecutionDetail
  trace: string | null
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function ExecutionDetailPage() {
  const { execution_id } = useParams<{ execution_id: string }>()
  const { data, isLoading, error } = useQuery<ExecutionDetailResponse>({
    queryKey: ['execution', execution_id],
    queryFn: () => apiFetch<ExecutionDetailResponse>(`/api/executions/${execution_id}`),
    enabled: !!execution_id,
  })

  if (isLoading) return <div className="loading-state">Loading execution...</div>
  if (error) return <div className="error-state">Failed to load execution</div>
  if (!data) return <div className="error-state">Execution not found</div>

  const { execution, trace } = data

  return (
    <div className="page">
      <header className="page-header">
        <h1>Execution</h1>
        <Link to={`/tasks/${execution.task_id}`} className="btn btn-secondary">
          ← Back to Task
        </Link>
      </header>

      <div className="modal-meta-row">
        <div className="modal-field">
          <div className="modal-label">Status</div>
          <div className="modal-value">
            <span className={`badge badge-${execution.status}`}>{execution.status}</span>
          </div>
        </div>
        <div className="modal-field">
          <div className="modal-label">Branch</div>
          <div className="modal-value modal-value-sm">{execution.branch_name ?? '—'}</div>
        </div>
        <div className="modal-field">
          <div className="modal-label">Started</div>
          <div className="modal-value modal-value-sm">{formatDate(execution.started_at)}</div>
        </div>
        <div className="modal-field">
          <div className="modal-label">Completed</div>
          <div className="modal-value modal-value-sm">{formatDate(execution.completed_at)}</div>
        </div>
      </div>

      <div className="modal-meta-row">
        <div className="modal-field">
          <div className="modal-label">Task</div>
          <div className="modal-value modal-value-sm">
            <Link to={`/tasks/${execution.task_id}`} className="link-soft">{execution.task_id}</Link>
          </div>
        </div>
        <div className="modal-field">
          <div className="modal-label">Spec</div>
          <div className="modal-value modal-value-sm">
            <Link to={`/specs/${execution.spec_id}`} className="link-soft">{execution.spec_id}</Link>
          </div>
        </div>
      </div>

      {execution.failure_reason && (
        <div className="modal-field">
          <div className="modal-label">Failure Reason</div>
          <pre className="modal-pre modal-pre-error">{execution.failure_reason}</pre>
        </div>
      )}

      {trace && (
        <div className="modal-field">
          <div className="modal-label">Trace</div>
          <div className="dark-surface">
            <Markdown content={trace} />
          </div>
        </div>
      )}
    </div>
  )
}
