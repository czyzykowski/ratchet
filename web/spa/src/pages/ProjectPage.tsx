import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchProject, type Task } from '../api/projects'
import { useSSE } from '../hooks/useSSE'
import { TaskDetailModal } from '../components/TaskDetailModal'
import { NewTaskModal } from '../components/NewTaskModal'
import { STATUS_COLORS } from '../utils/statusColors'

const STATUS_ORDER = [
  'ready_for_spec',
  'spec_qa',
  'ready_for_implementation',
  'in_progress',
  'blocked',
  'ready_for_qa',
  'ready_for_deployment',
  'deployed',
]

const STATUS_LABELS: Record<string, string> = {
  ready_for_spec: 'Ready for Spec',
  spec_qa: 'Spec QA',
  ready_for_implementation: 'Ready for Implementation',
  in_progress: 'In Progress',
  blocked: 'Blocked',
  ready_for_qa: 'Ready for QA',
  ready_for_deployment: 'Ready for Deployment',
  deployed: 'Deployed',
}

function formatRelativeTime(isoString: string | null | undefined): string {
  if (!isoString) return 'unknown'
  const date = new Date(isoString)
  const diffMs = Date.now() - date.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour}h ago`
  return `${Math.floor(diffHour / 24)}d ago`
}

function groupByStatus(tasks: Task[]): Record<string, Task[]> {
  const groups: Record<string, Task[]> = {}
  for (const status of STATUS_ORDER) {
    groups[status] = []
  }
  for (const task of tasks) {
    if (task.status in groups) {
      groups[task.status].push(task)
    }
  }
  return groups
}

export function ProjectPage() {
  const { project_id } = useParams<{ project_id: string }>()
  const queryClient = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['project', project_id],
    queryFn: () => fetchProject(project_id!),
    enabled: !!project_id,
  })
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [taskModalOpen, setTaskModalOpen] = useState(false)

  useSSE((event) => {
    if (
      event.type === 'task_updated' &&
      typeof event.project_id === 'string' &&
      event.project_id === project_id
    ) {
      queryClient.invalidateQueries({ queryKey: ['project', project_id] })
    }
  })

  if (isLoading) return <div className="loading-state">Loading project...</div>
  if (error) return <div className="error-state">Failed to load project</div>
  if (!data) return <div className="error-state">Project not found</div>

  const groups = groupByStatus(data.tasks)

  return (
    <div className="page">
      <header className="page-header">
        <h1>{data.project.name}</h1>
        <button className="btn btn-primary" onClick={() => setTaskModalOpen(true)}>
          Add Task
        </button>
      </header>
      {STATUS_ORDER.map(status => {
        const tasks = groups[status]
        if (tasks.length === 0) return null
        return (
          <section key={status} className="status-section">
            <h2 className="status-heading">{STATUS_LABELS[status] ?? status} ({tasks.length})</h2>
            <table className="data-table">
              <tbody>
                {tasks.map(task => (
                  <tr
                    key={task.id}
                    className="task-row"
                    onClick={() => setSelectedTaskId(task.id)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td>{task.title}</td>
                    <td>
                      <span
                        className="status-badge"
                        style={{ backgroundColor: STATUS_COLORS[task.status] ?? '#6b7280' }}
                      >
                        {task.status.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td>{formatRelativeTime(task.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )
      })}
      <TaskDetailModal
        taskId={selectedTaskId}
        onClose={() => setSelectedTaskId(null)}
      />
      {project_id && (
        <NewTaskModal
          open={taskModalOpen}
          projectId={project_id}
          onClose={() => setTaskModalOpen(false)}
        />
      )}
    </div>
  )
}
