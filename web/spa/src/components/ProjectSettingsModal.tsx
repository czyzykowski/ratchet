import { useState, FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { updateProject, type Project } from '../api/projects'

interface ProjectSettingsModalProps {
  project: Project
  open: boolean
  onClose: () => void
}

export function ProjectSettingsModal({ project, open, onClose }: ProjectSettingsModalProps) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(project.name)
  const [repoUrl, setRepoUrl] = useState(project.repo_url)
  const [localPath, setLocalPath] = useState(project.local_path)
  const [configSource, setConfigSource] = useState(project.config_source)
  const [claudeMd, setClaudeMd] = useState(project.claude_md ?? '')
  const [intentMd, setIntentMd] = useState(project.intent_md ?? '')
  const [ratchetYaml, setRatchetYaml] = useState(project.ratchet_yaml ?? '')
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
      await updateProject(project.id, {
        name,
        repo_url: repoUrl,
        local_path: localPath,
        config_source: configSource,
        claude_md: configSource === 'db' ? claudeMd || null : null,
        intent_md: configSource === 'db' ? intentMd || null : null,
        ratchet_yaml: configSource === 'db' ? ratchetYaml || null : null,
      })
      await queryClient.invalidateQueries({ queryKey: ['project', project.id] })
      onClose()
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string }
      setError(e.detail ?? e.message ?? 'Failed to update project')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content">
        <div className="modal-header">
          <div className="modal-title">Project Settings</div>
          <button className="modal-close" onClick={onClose}>&#215;</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-field">
            <label className="modal-label" htmlFor="settings-name">Name</label>
            <input
              id="settings-name"
              className="form-input"
              value={name}
              onChange={e => setName(e.target.value)}
              required
            />
          </div>
          <div className="modal-field">
            <label className="modal-label" htmlFor="settings-repo-url">Repo URL</label>
            <input
              id="settings-repo-url"
              className="form-input"
              value={repoUrl}
              onChange={e => setRepoUrl(e.target.value)}
              required
            />
          </div>
          <div className="modal-field">
            <label className="modal-label" htmlFor="settings-local-path">Local Path</label>
            <input
              id="settings-local-path"
              className="form-input"
              value={localPath}
              onChange={e => setLocalPath(e.target.value)}
              required
            />
          </div>
          <div className="modal-field">
            <label className="modal-label" htmlFor="settings-config-source">Config Source</label>
            <select
              id="settings-config-source"
              className="form-input"
              value={configSource}
              onChange={e => setConfigSource(e.target.value)}
            >
              <option value="disk">disk</option>
              <option value="db">db</option>
            </select>
          </div>
          {configSource === 'db' && (
            <>
              <div className="modal-field">
                <label className="modal-label" htmlFor="settings-claude-md">CLAUDE.md</label>
                <textarea
                  id="settings-claude-md"
                  className="form-input"
                  style={{ fontFamily: 'monospace' }}
                  rows={8}
                  value={claudeMd}
                  onChange={e => setClaudeMd(e.target.value)}
                />
              </div>
              <div className="modal-field">
                <label className="modal-label" htmlFor="settings-intent-md">INTENT.md</label>
                <textarea
                  id="settings-intent-md"
                  className="form-input"
                  style={{ fontFamily: 'monospace' }}
                  rows={8}
                  value={intentMd}
                  onChange={e => setIntentMd(e.target.value)}
                />
              </div>
              <div className="modal-field">
                <label className="modal-label" htmlFor="settings-ratchet-yaml">ratchet.yaml</label>
                <textarea
                  id="settings-ratchet-yaml"
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
              {submitting ? 'Saving...' : 'Save Settings'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
