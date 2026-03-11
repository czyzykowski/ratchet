import { useParams, Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { Markdown } from '../components/Markdown'

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
  baseline_qa_failure: string | null
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function TaskDetailPage() {
  const { task_id } = useParams<{ task_id: string }>()
  const queryClient = useQueryClient()
  const { data, isLoading, error } = useQuery<TaskDetailResponse>({
    queryKey: ['task', task_id],
    queryFn: () => apiFetch<TaskDetailResponse>(`/api/tasks/${task_id}`),
    enabled: !!task_id,
  })

  async function handleReset() {
    if (!task_id) return
    await fetch(`/api/tasks/${task_id}/reset`, { method: 'POST' })
    queryClient.invalidateQueries({ queryKey: ['task', task_id] })
  }

  async function handleDeploy() {
    if (!task_id) return
    await fetch(`/api/tasks/${task_id}/deploy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skip_merge: false }),
    })
    queryClient.invalidateQueries({ queryKey: ['task', task_id] })
  }

  if (isLoading) return <div className="loading-state">Loading task...</div>
  if (error) return <div className="error-state">Failed to load task</div>
  if (!data) return <div className="error-state">Task not found</div>

  const { task, specs, executions, qa_failure, baseline_qa_failure } = data
  const latestSpec = specs[specs.length - 1] ?? null

  return (
    <div className="page">
      <header className="page-header">
        <h1>{task.title}</h1>
        <Link to="/" className="btn btn-secondary">← Back</Link>
      </header>

      <div className="modal-meta-row">
        <div className="modal-field">
          <div className="modal-label">Status</div>
          <div className="modal-value">
            <span className={`badge badge-${task.status}`}>{task.status.replace(/_/g, ' ')}</span>
          </div>
        </div>
        <div className="modal-field">
          <div className="modal-label">Project</div>
          <div className="modal-value">{data.project_name ?? task.project_id}</div>
        </div>
        <div className="modal-field">
          <div className="modal-label">Refinements</div>
          <div className="modal-value">{task.refinement_count}</div>
        </div>
      </div>

      <div className="modal-meta-row">
        <div className="modal-field">
          <div className="modal-label">Created</div>
          <div className="modal-value modal-value-sm">{formatDate(task.created_at)}</div>
        </div>
        <div className="modal-field">
          <div className="modal-label">Updated</div>
          <div className="modal-value modal-value-sm">{formatDate(task.updated_at)}</div>
        </div>
      </div>

      {baseline_qa_failure && (
        <div className="modal-field">
          <div className="modal-label">Baseline QA Failure</div>
          <pre className="modal-pre modal-pre-error">{baseline_qa_failure}</pre>
        </div>
      )}

      {qa_failure && (
        <div className="modal-field">
          <div className="modal-label">QA Failure</div>
          <pre className="modal-pre modal-pre-error">{qa_failure}</pre>
        </div>
      )}

      {latestSpec && (
        <div className="modal-field">
          <div className="modal-label">
            Current Spec
            {specs.length > 1 && (
              <span style={{ marginLeft: '0.5rem', color: '#a0a0a0' }}>(rev {specs.length})</span>
            )}
            {' '}
            <Link to={`/specs/${latestSpec.id}`} style={{ fontSize: '0.75rem', color: '#7eb8f7' }}>
              view spec
            </Link>
          </div>
          <div style={{ border: '1px solid #2a2a2a', borderRadius: 4, padding: '0.75rem', background: '#111' }}>
            <Markdown content={latestSpec.content} />
          </div>
        </div>
      )}

      {specs.length > 1 && (
        <div className="modal-field">
          <div className="modal-label">Spec History</div>
          <ul style={{ margin: 0, paddingLeft: '1.25rem', color: '#a0a0a0', fontSize: '0.8rem' }}>
            {[...specs].reverse().map((s, i) => (
              <li key={s.id}>
                <Link to={`/specs/${s.id}`} style={{ color: '#7eb8f7' }}>
                  Rev {specs.length - i} — {formatDate(s.created_at)}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {executions.length > 0 && (
        <div className="modal-field">
          <div className="modal-label">Execution History ({executions.length})</div>
          <table className="modal-table">
            <tbody>
              {executions.map(ex => (
                <tr key={ex.id}>
                  <td>
                    <Link to={`/executions/${ex.id}`} style={{ color: '#7eb8f7' }}>
                      <span className={`badge badge-${ex.status}`}>{ex.status}</span>
                    </Link>
                  </td>
                  <td style={{ color: '#a0a0a0', fontSize: '0.75rem' }}>{ex.branch_name ?? '—'}</td>
                  <td style={{ color: '#a0a0a0', fontSize: '0.75rem' }}>{formatDate(ex.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.dependencies.length > 0 && (
        <div className="modal-field">
          <div className="modal-label">Dependencies</div>
          <ul style={{ margin: 0, paddingLeft: '1.25rem', color: '#a0a0a0', fontSize: '0.8rem' }}>
            {data.dependencies.map(dep => (
              <li key={dep}>
                <Link to={`/tasks/${dep}`} style={{ color: '#7eb8f7' }}>{dep}</Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="modal-actions" style={{ marginTop: '1rem' }}>
        {task.status === 'blocked' && (
          <button className="btn btn-danger" onClick={handleReset}>Reset Task</button>
        )}
        {task.status === 'ready_for_deployment' && (
          <button className="btn btn-primary" onClick={handleDeploy}>Deploy</button>
        )}
      </div>
    </div>
  )
}
