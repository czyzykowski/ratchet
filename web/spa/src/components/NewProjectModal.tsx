import { useState, FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { createProject } from '../api/projects'

interface NewProjectModalProps {
  open: boolean
  onClose: () => void
}

export function NewProjectModal({ open, onClose }: NewProjectModalProps) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
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
      await createProject(name, path)
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      setName('')
      setPath('')
      onClose()
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string }
      setError(e.detail ?? e.message ?? 'Failed to create project')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content">
        <div className="modal-header">
          <div className="modal-title">New Project</div>
          <button className="modal-close" onClick={onClose}>&#215;</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-field">
            <label className="modal-label" htmlFor="project-name">Name</label>
            <input
              id="project-name"
              className="form-input"
              value={name}
              onChange={e => setName(e.target.value)}
              required
              placeholder="My Project"
            />
          </div>
          <div className="modal-field">
            <label className="modal-label" htmlFor="project-path">Path</label>
            <input
              id="project-path"
              className="form-input"
              value={path}
              onChange={e => setPath(e.target.value)}
              required
              placeholder="/path/to/repo"
            />
          </div>
          {error && <div className="error-state">{error}</div>}
          <div className="modal-field">
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Creating...' : 'Create Project'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
