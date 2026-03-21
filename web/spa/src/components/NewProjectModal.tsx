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
  const [repoUrl, setRepoUrl] = useState('')
  const [configSource, setConfigSource] = useState('disk')
  const [claudeMd, setClaudeMd] = useState('')
  const [intentMd, setIntentMd] = useState('')
  const [ratchetYaml, setRatchetYaml] = useState('')
  const [capabilities, setCapabilities] = useState('')
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
      const caps = capabilities.trim()
        ? capabilities.split(',').map(c => c.trim()).filter(Boolean)
        : []
      await createProject(name, path, {
        config_source: configSource,
        repo_url: repoUrl || undefined,
        claude_md: configSource === 'db' ? claudeMd || null : null,
        intent_md: configSource === 'db' ? intentMd || null : null,
        ratchet_yaml: configSource === 'db' ? ratchetYaml || null : null,
        required_capabilities: caps,
      })
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      setName('')
      setPath('')
      setRepoUrl('')
      setConfigSource('disk')
      setClaudeMd('')
      setIntentMd('')
      setRatchetYaml('')
      setCapabilities('')
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
          <div className="modal-field">
            <label className="modal-label" htmlFor="project-repo-url">Repo URL <span style={{ fontWeight: 'normal', opacity: 0.7 }}>(optional)</span></label>
            <input
              id="project-repo-url"
              className="form-input"
              value={repoUrl}
              onChange={e => setRepoUrl(e.target.value)}
              placeholder="https://github.com/org/repo"
            />
          </div>
          <div className="modal-field">
            <label className="modal-label" htmlFor="project-config-source">Config Source</label>
            <select
              id="project-config-source"
              className="form-input"
              value={configSource}
              onChange={e => setConfigSource(e.target.value)}
            >
              <option value="disk">disk</option>
              <option value="db">db</option>
            </select>
          </div>
          <div className="modal-field">
            <label className="modal-label" htmlFor="project-capabilities">Required Capabilities <span style={{ fontWeight: 'normal', opacity: 0.7 }}>(optional, comma-separated)</span></label>
            <input
              id="project-capabilities"
              className="form-input"
              value={capabilities}
              onChange={e => setCapabilities(e.target.value)}
              placeholder="e.g. osx, gpu"
            />
          </div>
          {configSource === 'db' && (
            <>
              <div className="modal-field">
                <label className="modal-label" htmlFor="project-claude-md">CLAUDE.md</label>
                <textarea
                  id="project-claude-md"
                  className="form-input"
                  style={{ fontFamily: 'monospace' }}
                  rows={8}
                  value={claudeMd}
                  onChange={e => setClaudeMd(e.target.value)}
                />
              </div>
              <div className="modal-field">
                <label className="modal-label" htmlFor="project-intent-md">INTENT.md</label>
                <textarea
                  id="project-intent-md"
                  className="form-input"
                  style={{ fontFamily: 'monospace' }}
                  rows={8}
                  value={intentMd}
                  onChange={e => setIntentMd(e.target.value)}
                />
              </div>
              <div className="modal-field">
                <label className="modal-label" htmlFor="project-ratchet-yaml">ratchet.yaml</label>
                <textarea
                  id="project-ratchet-yaml"
                  className="form-input"
                  style={{ fontFamily: 'monospace' }}
                  rows={8}
                  value={ratchetYaml}
                  onChange={e => setRatchetYaml(e.target.value)}
                />
              </div>
            </>
          )}
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
