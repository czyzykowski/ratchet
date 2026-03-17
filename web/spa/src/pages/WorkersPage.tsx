import { useEffect, useRef, useState } from 'react'
import {
  useRestartWorker,
  useStartWorker,
  useStopWorker,
  useUpdateWorkerSettings,
  useWorkerStatus,
} from '../hooks/useWorkerStatus'
import { useWorkerLogs } from '../hooks/useWorkerLogs'
import type { WorkerSettings } from '../api/worker'

function formatUptime(seconds: number | null): string {
  if (seconds == null) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

function formatLogTime(timestamp: string): string {
  try {
    const d = new Date(timestamp)
    return d.toLocaleTimeString('en-GB', { hour12: false })
  } catch {
    return timestamp
  }
}

function statusDotClass(status: string): string {
  switch (status) {
    case 'running': return 'worker-status-dot dot-running'
    case 'stopped': return 'worker-status-dot dot-stopped'
    case 'starting': return 'worker-status-dot dot-starting'
    case 'error': return 'worker-status-dot dot-error'
    default: return 'worker-status-dot dot-stopped'
  }
}

function logLevelClass(level: string): string {
  switch (level.toUpperCase()) {
    case 'ERROR': return 'log-level-error'
    case 'WARNING':
    case 'WARN': return 'log-level-warning'
    case 'DEBUG': return 'log-level-debug'
    default: return ''
  }
}

export function WorkersPage() {
  const { data: workerStatus, isLoading } = useWorkerStatus()
  const { logs } = useWorkerLogs()
  const startWorker = useStartWorker()
  const stopWorker = useStopWorker()
  const restartWorker = useRestartWorker()
  const updateSettings = useUpdateWorkerSettings()

  const [showSettings, setShowSettings] = useState(false)
  const [draftSettings, setDraftSettings] = useState<Partial<WorkerSettings>>({})
  const logRef = useRef<HTMLPreElement>(null)

  const status = workerStatus?.status ?? 'stopped'
  const isRunning = status === 'running'
  const isStopped = status === 'stopped'
  const isStarting = status === 'starting'

  // Populate draft when settings panel opens
  useEffect(() => {
    if (showSettings && workerStatus?.settings) {
      setDraftSettings({
        watchdog_timeout: workerStatus.settings.watchdog_timeout,
        max_workers: workerStatus.settings.max_workers,
        enabled: workerStatus.settings.enabled,
      })
    }
  }, [showSettings, workerStatus?.settings])

  // Auto-scroll log viewer
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [logs])

  function handleSaveSettings() {
    updateSettings.mutate(draftSettings, {
      onSuccess: () => setShowSettings(false),
    })
  }

  if (isLoading) {
    return (
      <div className="workers-page page">
        <div className="loading-state">Loading...</div>
      </div>
    )
  }

  return (
    <div className="workers-page page">
      <div className="page-header">
        <h1>Workers</h1>
      </div>

      <div className="worker-card-grid">
        <div className="worker-card">
          {/* Header row */}
          <div className="worker-card-header">
            <span className="worker-card-title">Embedded Worker</span>
            <span className={statusDotClass(status)} />
            <span className="worker-status-text">{status}</span>
            {workerStatus?.uptime_seconds != null && (
              <span className="worker-uptime">up {formatUptime(workerStatus.uptime_seconds)}</span>
            )}
          </div>

          {/* Error message */}
          {status === 'error' && workerStatus?.error_message && (
            <div className="worker-error-message">{workerStatus.error_message}</div>
          )}

          {/* Controls row */}
          <div className="worker-controls">
            <button
              className="btn btn-primary btn-sm"
              disabled={isRunning || isStarting || startWorker.isPending}
              onClick={() => startWorker.mutate()}
            >
              Start
            </button>
            <button
              className="btn btn-danger btn-sm"
              disabled={isStopped || stopWorker.isPending}
              onClick={() => stopWorker.mutate(true)}
            >
              Stop
            </button>
            <button
              className="btn btn-secondary btn-sm"
              disabled={isStopped || restartWorker.isPending}
              onClick={() => restartWorker.mutate(true)}
            >
              Restart
            </button>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setShowSettings(v => !v)}
            >
              {showSettings ? 'Hide Settings' : 'Settings'}
            </button>
          </div>

          {/* Settings panel */}
          {showSettings && workerStatus?.settings && (
            <div className="worker-settings-panel">
              <div className="worker-settings-grid">
                <label className="worker-settings-label">
                  Watchdog Timeout (s)
                  <input
                    type="number"
                    className="form-input"
                    value={draftSettings.watchdog_timeout ?? workerStatus.settings.watchdog_timeout}
                    onChange={e =>
                      setDraftSettings(d => ({ ...d, watchdog_timeout: Number(e.target.value) }))
                    }
                  />
                </label>
                <label className="worker-settings-label">
                  Max Workers
                  <input
                    type="number"
                    className="form-input"
                    value={draftSettings.max_workers ?? workerStatus.settings.max_workers}
                    onChange={e =>
                      setDraftSettings(d => ({ ...d, max_workers: Number(e.target.value) }))
                    }
                  />
                </label>
                <label className="worker-settings-label worker-settings-checkbox">
                  <input
                    type="checkbox"
                    checked={draftSettings.enabled ?? workerStatus.settings.enabled}
                    onChange={e =>
                      setDraftSettings(d => ({ ...d, enabled: e.target.checked }))
                    }
                  />
                  Enabled
                </label>
              </div>
              <div className="worker-settings-actions">
                <button
                  className="btn btn-primary btn-sm"
                  disabled={updateSettings.isPending}
                  onClick={handleSaveSettings}
                >
                  {updateSettings.isPending ? 'Saving…' : 'Save'}
                </button>
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => setShowSettings(false)}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Log viewer */}
          <pre className="worker-log-viewer" ref={logRef}>
            {logs.length === 0
              ? <span className="log-level-debug">No log entries yet.</span>
              : logs.map((entry, i) => (
                <span key={i} className={logLevelClass(entry.level)}>
                  [{formatLogTime(entry.timestamp)}] [{entry.level.toUpperCase()}] {entry.message}{'\n'}
                </span>
              ))
            }
          </pre>
        </div>
      </div>
    </div>
  )
}
