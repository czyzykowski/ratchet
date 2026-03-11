import { useQuery } from '@tanstack/react-query'

interface TaskDetailModalProps {
  taskId: string | null
  onClose: () => void
}

interface TaskDetail {
  id: string
  title: string
  status: string
  project_id: string
  created_at: string
  updated_at: string | null
  current_spec_id: string | null
  refinement_count: number
  depends_on: string[]
}

interface Spec {
  id: string
  content: string
  created_at: string
}

interface Execution {
  id: string
  status: string
  failure_reason: string | null
  branch_name: string | null
  started_at: string
  completed_at: string | null
}

interface TaskDetailResponse {
  task: TaskDetail
  project_name: string | null
  specs: Spec[]
  executions: Execution[]
  dependencies: string[]
  qa_failure: string | null
}

async function fetchTaskDetail(taskId: string): Promise<TaskDetailResponse> {
  const res = await fetch(`/api/tasks/${taskId}`)
  if (!res.ok) throw new Error('Task not found')
  return res.json()
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function TaskDetailModal({ taskId, onClose }: TaskDetailModalProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => fetchTaskDetail(taskId!),
    enabled: taskId !== null,
  })

  if (!taskId) return null

  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === e.currentTarget) onClose()
  }

  const latestSpec = data?.specs[data.specs.length - 1] ?? null
  const latestExecution = data?.executions[data.executions.length - 1] ?? null

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content modal-content-wide">
        <div className="modal-header">
          <div className="modal-title">
            {isLoading ? 'Loading...' : error ? 'Error' : data?.task.title}
          </div>
          <button className="modal-close" onClick={onClose}>&#215;</button>
        </div>
        {isLoading && <div className="loading-state">Loading task details...</div>}
        {error && <div className="error-state">Failed to load task</div>}
        {data && (
          <>
            <div className="modal-meta-row">
              <div className="modal-field">
                <div className="modal-label">Status</div>
                <div className="modal-value">
                  <span className={`badge badge-${data.task.status}`}>
                    {data.task.status.replace(/_/g, ' ')}
                  </span>
                </div>
              </div>
              <div className="modal-field">
                <div className="modal-label">Project</div>
                <div className="modal-value">{data.project_name ?? data.task.project_id}</div>
              </div>
              <div className="modal-field">
                <div className="modal-label">Refinements</div>
                <div className="modal-value">{data.task.refinement_count}</div>
              </div>
            </div>

            <div className="modal-meta-row">
              <div className="modal-field">
                <div className="modal-label">Created</div>
                <div className="modal-value modal-value-sm">{formatDate(data.task.created_at)}</div>
              </div>
              <div className="modal-field">
                <div className="modal-label">Updated</div>
                <div className="modal-value modal-value-sm">{formatDate(data.task.updated_at)}</div>
              </div>
              {latestExecution && (
                <div className="modal-field">
                  <div className="modal-label">Last Execution</div>
                  <div className="modal-value modal-value-sm">
                    <span className={`badge badge-${latestExecution.status}`}>{latestExecution.status}</span>
                    {latestExecution.branch_name && (
                      <span style={{ marginLeft: '0.5rem', color: '#a0a0a0', fontSize: '0.75rem' }}>
                        {latestExecution.branch_name}
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>

            {data.qa_failure && (
              <div className="modal-field">
                <div className="modal-label">QA Failure</div>
                <pre className="modal-pre modal-pre-error">{data.qa_failure}</pre>
              </div>
            )}

            {latestExecution?.failure_reason && !data.qa_failure && (
              <div className="modal-field">
                <div className="modal-label">Failure Reason</div>
                <pre className="modal-pre modal-pre-error">{latestExecution.failure_reason}</pre>
              </div>
            )}

            {latestSpec && (
              <div className="modal-field">
                <div className="modal-label">
                  Current Spec
                  {data.specs.length > 1 && (
                    <span style={{ marginLeft: '0.5rem', color: '#a0a0a0' }}>
                      (rev {data.specs.length})
                    </span>
                  )}
                </div>
                <pre className="modal-pre">{latestSpec.content}</pre>
              </div>
            )}

            {data.executions.length > 1 && (
              <div className="modal-field">
                <div className="modal-label">Execution History ({data.executions.length})</div>
                <table className="modal-table">
                  <tbody>
                    {[...data.executions].reverse().map(ex => (
                      <tr key={ex.id}>
                        <td>
                          <span className={`badge badge-${ex.status}`}>{ex.status}</span>
                        </td>
                        <td style={{ color: '#a0a0a0', fontSize: '0.75rem' }}>
                          {ex.branch_name ?? '—'}
                        </td>
                        <td style={{ color: '#a0a0a0', fontSize: '0.75rem' }}>
                          {formatDate(ex.started_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
