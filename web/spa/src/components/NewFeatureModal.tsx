import { useState, FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'

interface NewFeatureModalProps {
  open: boolean
  projectId: string
  onClose: () => void
}

export function NewFeatureModal({ open, projectId, onClose }: NewFeatureModalProps) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
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
      const res = await fetch('/api/features/simple', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          title,
          description: description || undefined,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? 'Failed to create feature')
      }
      await queryClient.invalidateQueries({ queryKey: ['project-features', projectId] })
      await queryClient.invalidateQueries({ queryKey: ['features'] })
      setTitle('')
      setDescription('')
      onClose()
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string }
      setError(e.detail ?? e.message ?? 'Failed to create feature')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={handleOverlayClick} role="dialog" aria-modal="true">
      <div className="modal-content">
        <div className="modal-header">
          <div className="modal-title">Add Feature</div>
          <button className="modal-close" onClick={onClose} aria-label="Close">&#215;</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-field">
            <label className="modal-label" htmlFor="feature-title">Title</label>
            <input
              id="feature-title"
              className="form-input"
              value={title}
              onChange={e => setTitle(e.target.value)}
              required
              placeholder="Feature title"
            />
          </div>
          <div className="modal-field">
            <label className="modal-label" htmlFor="feature-description">Description (optional)</label>
            <textarea
              id="feature-description"
              className="form-input"
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Describe the feature..."
              rows={4}
            />
          </div>
          {error && <div className="error-state">{error}</div>}
          <div className="modal-field">
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Adding...' : 'Add Feature'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
