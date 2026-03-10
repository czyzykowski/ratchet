import { useState, FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { createTask } from '../api/projects'

interface NewTaskModalProps {
  open: boolean
  projectId: string
  onClose: () => void
}

export function NewTaskModal({ open, projectId, onClose }: NewTaskModalProps) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!open) return null

  function handleOverlayClick(e: React.MouseEvent) {
    if (e.target === e.currentTarget) onClose()
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await createTask(projectId, title)
      await queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      setTitle('')
      onClose()
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string }
      setError(e.detail ?? e.message ?? 'Failed to create task')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content">
        <div className="modal-header">
          <div className="modal-title">Add Task</div>
          <button className="modal-close" onClick={onClose}>&#215;</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-field">
            <label className="modal-label" htmlFor="task-title">Title</label>
            <input
              id="task-title"
              className="form-input"
              value={title}
              onChange={e => setTitle(e.target.value)}
              required
              placeholder="Task title"
            />
          </div>
          {error && <div className="error-state">{error}</div>}
          <div className="modal-field">
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Adding...' : 'Add Task'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
