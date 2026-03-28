import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CreateSpecChat } from './CreateSpecChat'
import { Markdown } from './Markdown'
import { fetchTaskQA, submitAnswer } from '../api/qa'
import { capabilityColor } from '../utils/capabilityColor'
import { useSSE } from '../hooks/useSSE'

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

interface SessionProgressData {
  messages: Record<string, unknown>[]
  total_messages: number
  file_size_bytes: number
}

async function fetchSessionProgress(executionId: string): Promise<SessionProgressData> {
  const res = await fetch(`/api/executions/${executionId}/session-progress`)
  if (!res.ok) throw new Error(`${res.status}`)
  return res.json()
}

function SessionProgressPanel({ executionId, enabled }: { executionId: string; enabled: boolean }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['session-progress', executionId],
    queryFn: () => fetchSessionProgress(executionId),
    enabled,
    refetchInterval: enabled ? 8000 : false,
    retry: false,
  })

  if (!enabled) return null

  const is404 = error instanceof Error && error.message === '404'

  return (
    <div className="modal-field">
      <div className="modal-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        Live Session
        {(isLoading || is404) && <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--color-primary)', display: 'inline-block', animation: 'pulse 1.5s infinite' }} />}
        {data && (
          <span className="text-secondary" style={{ fontSize: '0.75rem', fontWeight: 'normal' }}>
            {data.total_messages} messages · {data.file_size_bytes} bytes
          </span>
        )}
      </div>
      <div className="session-terminal">
        {is404 && (
          <div className="session-terminal-placeholder">Waiting for execution to start...</div>
        )}
        {!is404 && isLoading && !data && (
          <div className="session-terminal-placeholder">Connecting to session...</div>
        )}
        {data && data.messages.length === 0 && (
          <div className="session-terminal-placeholder">No messages yet...</div>
        )}
        {data && data.messages.map((msg, i) => {
          const msgType = msg.type as string
          const content = msg.content
          let preview = ''
          let label = msgType
          let labelColor = '#a0a0a0'

          if (msgType === 'assistant' && typeof content === 'string') {
            preview = content.slice(0, 200)
            labelColor = '#7eb8f7'
          } else if (msgType === 'assistant' && Array.isArray(content)) {
            const text = content.find((b: Record<string, unknown>) => b.type === 'text')
            preview = typeof text?.text === 'string' ? text.text.slice(0, 200) : ''
            labelColor = '#7eb8f7'
          } else if (msgType === 'tool_use') {
            const toolName = msg.name as string | undefined
            label = `tool: ${toolName ?? '?'}`
            const input = msg.input as Record<string, unknown> | undefined
            const filePath = input?.file_path ?? input?.path ?? input?.command
            preview = typeof filePath === 'string' ? filePath.slice(0, 100) : ''
            labelColor = '#f9c74f'
          } else if (msgType === 'tool_result') {
            const isError = msg.is_error
            label = isError ? 'tool_result: error' : 'tool_result: ok'
            labelColor = isError ? '#f44336' : '#4caf50'
          } else if (typeof content === 'string') {
            preview = content.slice(0, 200)
          }

          return (
            <div key={i} className="session-terminal-line">
              <span style={{ color: labelColor, marginRight: '0.5rem' }}>[{label}]</span>
              {preview && <span style={{ color: '#ccc' }}>{preview}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

const ARCHIVABLE_STATUSES = [
  'ready_for_spec',
  'spec_qa',
  'ready_for_implementation',
  'in_progress',
  'waiting_for_input',
  'ready_for_qa',
  'ready_for_merge',
  'blocked',
  'deployed',
]

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
    refetchInterval: (query) => {
      const status = query.state.data?.task.status
      // Poll every 5s for active tasks so Live Session panel appears
      return status === 'in_progress' || status === 'ready_for_qa' || status === 'ready_for_merge'
        ? 5000 : false
    },
  })

  useSSE((event) => {
    if (event.type === 'task_updated' && taskId) {
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
    }
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
  const [showArchive, setShowArchive] = useState(false)
  const [archiveReason, setArchiveReason] = useState('')
  const [archiving, setArchiving] = useState(false)
  const [archiveError, setArchiveError] = useState<string | null>(null)
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

  function openArchiveForm() {
    setArchiveReason('')
    setArchiveError(null)
    setShowArchive(true)
  }

  function cancelArchive() {
    setShowArchive(false)
    setArchiveError(null)
  }

  async function confirmArchive() {
    if (!taskId) return
    setArchiving(true)
    setArchiveError(null)
    try {
      const res = await fetch(`/api/tasks/${taskId}/archive`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: archiveReason || null }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? 'Archive failed')
      }
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
      queryClient.invalidateQueries({ queryKey: ['board'] })
      onClose()
    } catch (err) {
      setArchiveError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setArchiving(false)
    }
  }

  const latestSpec = data?.specs[data.specs.length - 1] ?? null
  const latestExecution = data?.executions[data.executions.length - 1] ?? null
  const isBlocked = data?.task.status === 'blocked'
  const isReadyForSpec = data?.task.status === 'ready_for_spec'
  const isReadyForDeployment = data?.task.status === 'ready_for_merge'
  const isArchivable = ARCHIVABLE_STATUSES.includes(data?.task.status ?? '')

  if (showChat && data) {
    return (
      <div className="modal-overlay" onClick={handleOverlayClick} role="dialog" aria-modal="true">
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
    <div className="modal-overlay" onClick={handleOverlayClick} role="dialog" aria-modal="true">
      <div className="modal-content modal-content-wide">
        <div className="modal-header">
          <div className="modal-title">
            {isLoading ? 'Loading...' : error ? 'Error' : data?.task.title}
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">&#215;</button>
        </div>
        {isLoading && <div className="loading-state">Loading task details...</div>}
        {error && <div className="error-state">Failed to load task</div>}
        {data && !showReset && !showArchive && (
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
              {data.feature_id && data.feature_title && (
                <div className="modal-field">
                  <div className="modal-label">Feature</div>
                  <div className="modal-value">
                    <Link to={`/features/${data.feature_id}`} style={{ color: '#7eb8f7' }}>
                      {data.feature_title}
                    </Link>
                  </div>
                </div>
              )}
              {(data.task.required_capabilities ?? []).length > 0 && (
                <div className="modal-field">
                  <div className="modal-label">Capabilities</div>
                  <div className="modal-value" style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
                    {(data.task.required_capabilities ?? []).map(cap => {
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
                    <Link to={`/executions/${latestExecution.id}`} style={{ color: '#7eb8f7' }}>
                      <span className={`badge badge-${latestExecution.status}`}>{latestExecution.status}</span>
                    </Link>
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
                          <Link to={`/executions/${ex.id}`} style={{ color: '#7eb8f7' }}>
                            <span className={`badge badge-${ex.status}`}>{ex.status}</span>
                          </Link>
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

            {data.task.status === 'in_progress' && latestExecution && (
              <SessionProgressPanel
                executionId={latestExecution.id}
                enabled={data.task.status === 'in_progress'}
              />
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

            {(isBlocked || isReadyForSpec || isReadyForDeployment || isArchivable) && (
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
                    Merge
                  </button>
                )}
                {isArchivable && !showDeploy && (
                  <button className="btn btn-danger" onClick={openArchiveForm}>
                    Archive
                  </button>
                )}
              </div>
            )}
          </>
        )}

        {data && showDeploy && (
          <div className="reset-form">
            <div className="reset-form-header">Merge Task</div>

            <label className="reset-checkbox-label">
              <input
                type="checkbox"
                checked={skipMerge}
                onChange={e => setSkipMerge(e.target.checked)}
              />
              Skip merge (mark as merged without running git merge)
            </label>

            {deployError && (
              <pre className="modal-pre modal-pre-error">{deployError}</pre>
            )}

            <div className="reset-form-actions">
              <button className="btn btn-secondary" onClick={cancelDeploy} disabled={deploying}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={confirmDeploy} disabled={deploying}>
                {deploying ? 'Merging...' : 'Confirm Merge'}
              </button>
            </div>
          </div>
        )}

        {data && showArchive && (
          <div className="reset-form">
            <div className="reset-form-header">Archive Task</div>

            <textarea
              className="reset-spec-textarea"
              value={archiveReason}
              onChange={e => setArchiveReason(e.target.value)}
              rows={4}
              placeholder="Reason (optional)"
              disabled={archiving}
            />

            {archiveError && (
              <div className="error-state" style={{ padding: '0.5rem 0' }}>{archiveError}</div>
            )}

            <div className="reset-form-actions">
              <button className="btn btn-secondary" onClick={cancelArchive} disabled={archiving}>
                Cancel
              </button>
              <button className="btn btn-danger" onClick={confirmArchive} disabled={archiving}>
                {archiving ? 'Archiving...' : 'Confirm Archive'}
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
