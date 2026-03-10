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
  updated_at: string | null
  qa_failure?: string | null
}

interface TaskDetailResponse {
  task: TaskDetail
  qa_failure: string | null
}

async function fetchTaskDetail(taskId: string): Promise<TaskDetailResponse> {
  const res = await fetch(`/api/tasks/${taskId}`)
  if (!res.ok) throw new Error('Task not found')
  return res.json()
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

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content">
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
            <div className="modal-field">
              <div className="modal-label">Status</div>
              <div className="modal-value">
                <span className={`badge badge-${data.task.status}`}>
                  {data.task.status.replace(/_/g, ' ')}
                </span>
              </div>
            </div>
            {data.qa_failure && (
              <div className="modal-field">
                <div className="modal-label">QA Failure</div>
                <div className="modal-value" style={{ color: '#ff6b6b', fontSize: '0.8rem' }}>
                  {data.qa_failure}
                </div>
              </div>
            )}
            <div className="modal-field">
              <a href={`/tasks/${taskId}`} style={{ color: '#87ceeb', fontSize: '0.875rem' }}>
                View Full Details &#8594;
              </a>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
