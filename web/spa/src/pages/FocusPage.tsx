import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useBoard } from '../hooks/useBoard'
import { useActivity } from '../hooks/useActivity'
import { useSSE } from '../hooks/useSSE'
import { TaskDetailModal } from '../components/TaskDetailModal'

const ATTENTION_STATUSES = ['ready_for_spec', 'blocked', 'ready_for_deployment']

const STATUS_LABELS: Record<string, string> = {
  ready_for_spec: 'Ready for Spec',
  spec_qa: 'Spec QA',
  ready_for_implementation: 'Ready for Impl',
  in_progress: 'In Progress',
  blocked: 'Blocked',
  ready_for_qa: 'Ready for QA',
  ready_for_deployment: 'Ready to Deploy',
  deployed: 'Deployed',
  abandoned: 'Abandoned',
}

function formatRelativeTime(isoString: string | null): string {
  if (!isoString) return '—'
  const diffMs = Date.now() - new Date(isoString).getTime()
  const s = Math.floor(diffMs / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function fmt(status: string) {
  return STATUS_LABELS[status] ?? status.replace(/_/g, ' ')
}

export function FocusPage() {
  const queryClient = useQueryClient()
  const { data: board, isLoading: boardLoading } = useBoard()
  const { data: activity, isLoading: activityLoading } = useActivity()
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  useSSE(() => {
    queryClient.invalidateQueries({ queryKey: ['board'] })
    queryClient.invalidateQueries({ queryKey: ['activity'] })
  })

  function openDeployModal(e: React.MouseEvent, taskId: string) {
    e.stopPropagation()
    setSelectedTaskId(taskId)
  }

  const allTasks = board?.columns.flatMap(c => c.tasks) ?? []
  const attentionTasks = allTasks.filter(t => ATTENTION_STATUSES.includes(t.status))
  const pipelineColumns = board?.columns ?? []

  return (
    <div className="focus-page">
      <div className="focus-pipeline">
        {pipelineColumns.map(col => (
          <div
            key={col.status}
            className={`pipeline-stage${col.tasks.length === 0 ? ' pipeline-stage-empty' : ''}`}
          >
            <span className="pipeline-count">{col.tasks.length}</span>
            <span className="pipeline-label">{STATUS_LABELS[col.status] ?? col.label}</span>
          </div>
        ))}
      </div>

      <div className="focus-body">
        <div className="focus-section">
          <div className="focus-section-header">
            Needs Attention
            <span className="focus-section-count">{attentionTasks.length}</span>
          </div>
          {boardLoading && <div className="focus-empty">Loading...</div>}
          {!boardLoading && attentionTasks.length === 0 && (
            <div className="focus-empty">Nothing needs attention right now.</div>
          )}
          {attentionTasks.map(task => (
            <div
              key={task.id}
              className={`attention-row attention-row-${task.status}`}
              onClick={() => setSelectedTaskId(task.id)}
            >
              <span className={`badge badge-${task.status}`}>{fmt(task.status)}</span>
              <span className="attention-title">{task.title}</span>
              <span className="attention-meta">{task.project_name}</span>
              <span className="attention-meta">{formatRelativeTime(task.updated_at)}</span>
              {task.status === 'ready_for_deployment' && (
                <button
                  className="btn btn-primary btn-sm"
                  onClick={e => openDeployModal(e, task.id)}
                >
                  Deploy
                </button>
              )}
            </div>
          ))}
        </div>

        <div className="focus-section">
          <div className="focus-section-header">Recent Activity</div>
          {activityLoading && <div className="focus-empty">Loading...</div>}
          {!activityLoading && (activity?.events.length ?? 0) === 0 && (
            <div className="focus-empty">No activity yet.</div>
          )}
          {activity?.events.map((event, i) => (
            <div
              key={i}
              className="activity-row"
              onClick={() => setSelectedTaskId(event.task_id)}
            >
              <span className="activity-time">{formatRelativeTime(event.occurred_at)}</span>
              <span className="activity-title">{event.task_title}</span>
              <span className="activity-transition">
                {event.from_status && (
                  <>
                    <span className={`badge badge-${event.from_status}`}>{fmt(event.from_status)}</span>
                    <span className="activity-arrow">→</span>
                  </>
                )}
                <span className={`badge badge-${event.to_status}`}>{fmt(event.to_status)}</span>
              </span>
              <span className="activity-project">{event.project_name}</span>
            </div>
          ))}
        </div>
      </div>

      <TaskDetailModal
        taskId={selectedTaskId}
        onClose={() => setSelectedTaskId(null)}
      />
    </div>
  )
}
