import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CreateSpecChat } from './CreateSpecChat'
import { Markdown } from './Markdown'
import { fetchTaskQA, submitAnswer } from '../api/qa'

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
  const queryClient = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => fetchTaskDetail(taskId!),
    enabled: taskId !== null,
  })

  const [showChat, setShowChat] = useState(false)
  const [showReset, setShowReset] = useState(false)
  const [reuseSpec, setReuseSpec] = useState(true)
  const [specContent, setSpecContent] = useState('')
  const [resetting, setResetting] = useState(false)
  const [resetError, setResetError] = useState<string | null>(null)
  const [showDeploy, setShowDeploy] = useState(false)
  const [skipMerge, setSkipMerge] = useState(false)
  const [deploying, setDeploying] = useState(false)
  const [deployError, setDeployError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)
  const [forceExecuting, setForceExecuting] = useState(false)
  const [forceExecuteError, setForceExecuteError] = useState<string | null>(null)
  const [answerText, setAnswerText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const isWaitingForInput = data?.task.status === 'waiting_for_input'
  const { data: qaData } = useQuery({
    queryKey: ['task-qa', taskId],
    queryFn: () => fetchTaskQA(taskId!),
    enabled: taskId !== null && isWaitingForInput,
  })

  async function handleSubmitAnswer() {
    if (!taskId || !qaData?.pending) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      await submitAnswer(taskId, answerText, qaData.pending.question_index)
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
      queryClient.invalidateQueries({ queryKey: ['task-qa', taskId] })
      queryClient.invalidateQueries({ queryKey: ['board'] })
      setAnswerText('')
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setSubmitting(false)
    }
  }

  if (!taskId) return null

  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === e.currentTarget) onClose()
  }

  function openResetForm() {
    const latestSpec = data?.specs[data.specs.length - 1]
    setSpecContent(latestSpec?.content ?? '')
    setReuseSpec(true)
    setResetError(null)
    setShowReset(true)
  }

  function cancelReset() {
    setShowReset(false)
    setResetError(null)
  }

  async function confirmReset() {
    if (!taskId) return
    setResetting(true)
    setResetError(null)
    try {
      if (reuseSpec) {
        const res = await fetch(`/api/tasks/${taskId}/reset`, { method: 'POST' })
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          throw new Error(body.detail ?? 'Reset failed')
        }
      } else {
        const res = await fetch(`/api/tasks/${taskId}/spec`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: specContent }),
        })
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          throw new Error(body.detail ?? 'Spec update failed')
        }
      }
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
      queryClient.invalidateQueries({ queryKey: ['board'] })
      setShowReset(false)
      onClose()
    } catch (err) {
      setResetError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setResetting(false)
    }
  }

  function openDeployForm() {
    setSkipMerge(false)
    setDeployError(null)
    setShowDeploy(true)
  }

  function cancelDeploy() {
    setShowDeploy(false)
    setDeployError(null)
  }

  async function confirmDeploy() {
    if (!taskId) return
    setDeploying(true)
    setDeployError(null)
    try {
      const res = await fetch(`/api/tasks/${taskId}/merge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skip_merge: skipMerge }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? 'Deploy failed')
      }
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
      queryClient.invalidateQueries({ queryKey: ['board'] })
      onClose()
    } catch (err) {
      setDeployError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setDeploying(false)
    }
  }

  async function retryBaselineQa() {
    if (!taskId) return
    setRetrying(true)
    setRetryError(null)
    try {
      const res = await fetch(`/api/tasks/${taskId}/retry-baseline-qa`, { method: 'POST' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? 'Retry failed')
      }
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
      queryClient.invalidateQueries({ queryKey: ['board'] })
    } catch (err) {
      setRetryError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setRetrying(false)
    }
  }

  async function forceExecute() {
    if (!taskId) return
    setForceExecuting(true)
    setForceExecuteError(null)
    try {
      const res = await fetch(`/api/tasks/${taskId}/force-execute`, { method: 'POST' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? 'Force execute failed')
      }
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
      queryClient.invalidateQueries({ queryKey: ['board'] })
    } catch (err) {
      setForceExecuteError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setForceExecuting(false)
    }
  }

  const latestSpec = data?.specs[data.specs.length - 1] ?? null
  const latestExecution = data?.executions[data.executions.length - 1] ?? null
  const isBlocked = data?.task.status === 'blocked'
  const isReadyForSpec = data?.task.status === 'ready_for_spec'
  const isReadyForDeployment = data?.task.status === 'ready_for_merge'

  if (showChat && data) {
    return (
      <div className="modal-overlay" onClick={handleOverlayClick}>
        <div className="modal-content modal-content-chat">
          <CreateSpecChat
            taskId={taskId}
            taskTitle={data.task.title}
            onClose={() => { setShowChat(false); onClose() }}
          />
        </div>
      </div>
    )
  }

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
        {data && !showReset && (
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

            {isWaitingForInput && qaData && (
              <div className="qa-panel">
                <div className="modal-label">Question</div>
                <div className="qa-question">{qaData.pending?.question}</div>
                <textarea
                  className="reset-spec-textarea"
                  value={answerText}
                  onChange={e => setAnswerText(e.target.value)}
                  rows={4}
                  placeholder="Your answer…"
                  disabled={submitting}
                />
                {submitError && <div className="error-state">{submitError}</div>}
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <button
                    className="btn btn-primary"
                    onClick={handleSubmitAnswer}
                    disabled={submitting || !answerText.trim()}
                  >
                    {submitting ? 'Submitting…' : 'Submit Answer'}
                  </button>
                </div>
              </div>
            )}

            {qaData && qaData.history.filter(x => x.answer !== null).length > 0 && (
              <div className="modal-field">
                <div className="modal-label">
                  Q&amp;A History ({qaData.history.filter(x => x.answer !== null).length})
                </div>
                {qaData.history.filter(x => x.answer !== null).map(ex => (
                  <div key={ex.question_index} className="qa-history-item">
                    <div className="qa-question">{ex.question}</div>
                    <div className="qa-answer"><strong>Answer:</strong> {ex.answer}</div>
                  </div>
                ))}
              </div>
            )}

            {data.baseline_qa_failure && (
              <div className="modal-field">
                <div className="modal-label">Baseline QA Failure</div>
                <pre className="modal-pre modal-pre-error">{data.baseline_qa_failure}</pre>
                {retryError && (
                  <div className="error-state" style={{ padding: '0.5rem 0' }}>{retryError}</div>
                )}
                {forceExecuteError && (
                  <div className="error-state" style={{ padding: '0.5rem 0' }}>{forceExecuteError}</div>
                )}
                <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
                  <button
                    className="btn btn-secondary"
                    onClick={retryBaselineQa}
                    disabled={retrying || forceExecuting}
                  >
                    {retrying ? 'Retrying...' : 'Retry'}
                  </button>
                  <button
                    className="btn btn-danger"
                    onClick={forceExecute}
                    disabled={retrying || forceExecuting}
                  >
                    {forceExecuting ? 'Requesting...' : 'Force Execute (skip baseline QA)'}
                  </button>
                </div>
              </div>
            )}

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
                <div style={{ border: '1px solid #2a2a2a', borderRadius: 4, padding: '0.75rem', background: '#111' }}>
                  <Markdown content={latestSpec.content} />
                </div>
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

            {data.pr_info && (
              <div className="modal-field">
                <div className="modal-label">Deployment</div>
                <div>
                  <a
                    href={data.pr_info.pr_url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: '#7eb8f7' }}
                  >
                    PR #{data.pr_info.pr_number}
                  </a>
                  <span style={{ marginLeft: '0.5rem', color: '#a0a0a0', fontSize: '0.8rem' }}>
                    {data.pr_info.branch}
                  </span>
                </div>
              </div>
            )}

            {data.deploy_hooks && (
              <div className="modal-field">
                <div className="modal-label">Deployment</div>
                {data.deploy_hooks.map((step, i) => (
                  <div key={i} style={{ marginBottom: '0.75rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
                      <strong>{step.name}</strong>
                      <code style={{ color: '#a0a0a0', fontSize: '0.8rem' }}>{step.command}</code>
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

            {(isBlocked || isReadyForSpec || isReadyForDeployment) && (
              <div className="modal-actions">
                {isReadyForSpec && (
                  <button className="btn btn-primary" onClick={() => setShowChat(true)}>
                    Create Spec
                  </button>
                )}
                {isBlocked && (
                  <button className="btn btn-danger" onClick={openResetForm}>
                    Reset Task
                  </button>
                )}
                {isReadyForDeployment && (
                  <button className="btn btn-primary" onClick={openDeployForm}>
                    Deploy
                  </button>
                )}
              </div>
            )}
          </>
        )}

        {data && showDeploy && (
          <div className="reset-form">
            <div className="reset-form-header">Deploy Task</div>

            <label className="reset-checkbox-label">
              <input
                type="checkbox"
                checked={skipMerge}
                onChange={e => setSkipMerge(e.target.checked)}
              />
              Skip merge (mark as deployed without running git merge)
            </label>

            {deployError && (
              <pre className="modal-pre modal-pre-error">{deployError}</pre>
            )}

            <div className="reset-form-actions">
              <button className="btn btn-secondary" onClick={cancelDeploy} disabled={deploying}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={confirmDeploy} disabled={deploying}>
                {deploying ? 'Deploying...' : 'Confirm Deploy'}
              </button>
            </div>
          </div>
        )}

        {data && showReset && (
          <div className="reset-form">
            <div className="reset-form-header">Reset Blocked Task</div>

            <label className="reset-checkbox-label">
              <input
                type="checkbox"
                checked={reuseSpec}
                onChange={e => setReuseSpec(e.target.checked)}
              />
              Reuse current spec
            </label>

            <textarea
              className="reset-spec-textarea"
              value={specContent}
              onChange={e => setSpecContent(e.target.value)}
              readOnly={reuseSpec}
              rows={20}
            />

            {resetError && (
              <div className="error-state" style={{ padding: '0.5rem 0' }}>{resetError}</div>
            )}

            <div className="reset-form-actions">
              <button className="btn btn-secondary" onClick={cancelReset} disabled={resetting}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={confirmReset} disabled={resetting}>
                {resetting ? 'Resetting...' : 'Confirm Reset'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
