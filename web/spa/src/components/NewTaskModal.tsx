import { useState, FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { createTask } from '../api/projects'

interface NewTaskModalProps {
  open: boolean
  projectId: string
  projectCapabilities: string[]
  onClose: () => void
}

export function NewTaskModal({ open, projectId, projectCapabilities, onClose }: NewTaskModalProps) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [capabilities, setCapabilities] = useState(projectCapabilities.join(', '))
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
    const caps = capabilities.trim()
      ? capabilities.split(',').map(c => c.trim()).filter(Boolean)
      : []
    try {
      await createTask(projectId, title, caps)
      await queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      setTitle('')
      setCapabilities(projectCapabilities.join(', '))
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
          <div className="modal-field">
            <label className="modal-label" htmlFor="task-capabilities">Required Capabilities <span style={{ fontWeight: 'normal', opacity: 0.7 }}>(comma-separated)</span></label>
            <input
              id="task-capabilities"
              className="form-input"
              value={capabilities}
              onChange={e => setCapabilities(e.target.value)}
              placeholder="e.g. linux, gpu"
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
