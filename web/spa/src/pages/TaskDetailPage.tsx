import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { Markdown } from '../components/Markdown'
import { capabilityColor } from '../utils/capabilityColor'

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
  required_capabilities: string[]
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

interface PrInfo {
  pr_url: string
  pr_number: number
  branch: string
}

interface DeployHookStep {
  name: string
  command: string
  returncode: number
  output: string
}

interface TaskDetailResponse {
  task: TaskDetail
  project_name: string | null
  specs: Spec[]
  executions: Execution[]
  dependencies: string[]
  qa_failure: string | null
  baseline_qa_failure: string | null
  pr_info: PrInfo | null
  deploy_hooks: DeployHookStep[] | null
  feature_id: string | null
  feature_title: string | null
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function TaskDetailPage() {
  const { task_id } = useParams<{ task_id: string }>()
  const queryClient = useQueryClient()
  const [capabilitiesEdit, setCapabilitiesEdit] = useState<string | null>(null)
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

  async function handleSaveCapabilities() {
    if (!task_id || capabilitiesEdit === null) return
    const caps = capabilitiesEdit.trim()
      ? capabilitiesEdit.split(',').map(c => c.trim()).filter(Boolean)
      : []
    await fetch(`/api/tasks/${task_id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ required_capabilities: caps }),
    })
    setCapabilitiesEdit(null)
    queryClient.invalidateQueries({ queryKey: ['task', task_id] })
  }

  async function handleArchive() {
    if (!task_id) return
    if (!confirm('Archive this task?')) return
    await fetch(`/api/tasks/${task_id}/archive`, { method: 'POST' })
    queryClient.invalidateQueries({ queryKey: ['task', task_id] })
  }

  if (isLoading) return <div className="loading-state">Loading task...</div>
  if (error) return <div className="error-state">Failed to load task</div>
  if (!data) return <div className="error-state">Task not found</div>

  const { task, specs, executions, qa_failure, baseline_qa_failure, pr_info, deploy_hooks } = data
  const currentCapabilities = task.required_capabilities ?? []
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
        {data.feature_id && data.feature_title && (
          <div className="modal-field">
            <div className="modal-label">Feature</div>
            <div className="modal-value">
              <Link to={`/features/${data.feature_id}`} className="link-soft">
                {data.feature_title}
              </Link>
            </div>
          </div>
        )}
        {currentCapabilities.length > 0 && (
          <div className="modal-field">
            <div className="modal-label">Capabilities</div>
            <div className="modal-value" style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
              {currentCapabilities.map(cap => {
                const colors = capabilityColor(cap)
                return (
                  <span key={cap} className="badge" style={{ background: colors.background, color: colors.color }}>
                    {cap}
                  </span>
                )
              })}
            </div>
          </div>
        )}
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
              <span className="text-dim" style={{ marginLeft: '0.5rem' }}>(rev {specs.length})</span>
            )}
            {' '}
            <Link to={`/specs/${latestSpec.id}`} className="link-soft text-sm">
              view spec
            </Link>
          </div>
          <div className="dark-surface">
            <Markdown content={latestSpec.content} />
          </div>
        </div>
      )}

      {specs.length > 1 && (
        <div className="modal-field">
          <div className="modal-label">Spec History</div>
          <ul className="text-dim text-xs" style={{ margin: 0, paddingLeft: '1.25rem' }}>
            {[...specs].reverse().map((s, i) => (
              <li key={s.id}>
                <Link to={`/specs/${s.id}`} className="link-soft">
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
                    <Link to={`/executions/${ex.id}`} className="link-soft">
                      <span className={`badge badge-${ex.status}`}>{ex.status}</span>
                    </Link>
                  </td>
                  <td className="text-dim text-sm">{ex.branch_name ?? '—'}</td>
                  <td className="text-dim text-sm">{formatDate(ex.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pr_info && (
        <div className="modal-field">
          <div className="modal-label">Deployment</div>
          <div>
            <a href={pr_info.pr_url} target="_blank" rel="noreferrer" className="link-soft">
              PR #{pr_info.pr_number}
            </a>
            <span className="text-dim text-xs" style={{ marginLeft: '0.5rem' }}>
              {pr_info.branch}
            </span>
          </div>
        </div>
      )}

      {deploy_hooks && (
        <div className="modal-field">
          <div className="modal-label">Deployment</div>
          {deploy_hooks.map((step, i) => (
            <div key={i} style={{ marginBottom: '0.75rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
                <strong>{step.name}</strong>
                <code className="text-dim text-xs">{step.command}</code>
                <span
                  style={{
                    padding: '0.1rem 0.4rem',
                    borderRadius: 3,
                    fontSize: '0.75rem',
                    background: step.returncode === 0 ? '#1a4a1a' : '#4a1a1a',
                    color: step.returncode === 0 ? '#4caf50' : '#f44336',
                  }}
                >
                  {step.returncode}
                </span>
              </div>
              <pre className="modal-pre" style={{ margin: 0 }}>{step.output}</pre>
            </div>
          ))}
        </div>
      )}

      <div className="modal-field">
        <div className="modal-label">
          Required Capabilities
          {capabilitiesEdit === null && (
            <button
              style={{ marginLeft: '0.5rem', fontSize: '0.7rem', padding: '0.1rem 0.4rem', cursor: 'pointer' }}
              onClick={() => setCapabilitiesEdit(currentCapabilities.join(', '))}
            >edit</button>
          )}
        </div>
        {capabilitiesEdit !== null ? (
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <input
              className="form-input"
              style={{ flex: 1 }}
              value={capabilitiesEdit}
              onChange={e => setCapabilitiesEdit(e.target.value)}
              placeholder="comma-separated, e.g. osx, gpu"
            />
            <button className="btn btn-primary" style={{ padding: '0.25rem 0.6rem' }} onClick={handleSaveCapabilities}>Save</button>
            <button className="btn btn-secondary" style={{ padding: '0.25rem 0.6rem' }} onClick={() => setCapabilitiesEdit(null)}>Cancel</button>
          </div>
        ) : (
          <div className="modal-value" style={{ color: currentCapabilities.length ? undefined : '#666' }}>
            {currentCapabilities.length ? currentCapabilities.join(', ') : '(none)'}
          </div>
        )}
      </div>

      {data.dependencies.length > 0 && (
        <div className="modal-field">
          <div className="modal-label">Dependencies</div>
          <ul className="text-dim text-xs" style={{ margin: 0, paddingLeft: '1.25rem' }}>
            {data.dependencies.map(dep => (
              <li key={dep}>
                <Link to={`/tasks/${dep}`} className="link-soft">{dep}</Link>
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
        {task.status !== 'abandoned' && (
          <button className="btn btn-danger" onClick={handleArchive}>Archive</button>
        )}
      </div>
    </div>
  )
}
