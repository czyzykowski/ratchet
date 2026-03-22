import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  useRestartWorker,
  useStartWorker,
  useStopWorker,
  useUpdateWorkerSettings,
  useWorkerStatus,
} from '../hooks/useWorkerStatus'
import { useWorkerLogs } from '../hooks/useWorkerLogs'
import type { WorkerSettings } from '../api/worker'
import { fetchConnectedWorkers } from '../api/workers'
import type { ConnectedWorker } from '../api/workers'

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

function formatConnectedAt(isoString: string): string {
  try {
    return new Date(isoString).toLocaleTimeString('en-GB', { hour12: false })
  } catch {
    return isoString
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

function useConnectedWorkers() {
  return useQuery({
    queryKey: ['connected-workers'],
    queryFn: fetchConnectedWorkers,
    refetchInterval: 5_000,
    staleTime: 4_000,
  })
}

function ConnectedWorkersTable({ workers }: { workers: ConnectedWorker[] }) {
  if (workers.length === 0) {
    return <p className="log-level-debug" style={{ padding: '0.5rem 0' }}>No workers connected.</p>
  }
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
      <thead>
        <tr>
          <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem', borderBottom: '1px solid var(--border)' }}>Worker ID</th>
          <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem', borderBottom: '1px solid var(--border)' }}>Capabilities</th>
          <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem', borderBottom: '1px solid var(--border)' }}>Status</th>
          <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem', borderBottom: '1px solid var(--border)' }}>Current Task</th>
          <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem', borderBottom: '1px solid var(--border)' }}>Connected At</th>
        </tr>
      </thead>
      <tbody>
        {workers.map(w => (
          <tr key={w.id}>
            <td style={{ padding: '0.25rem 0.5rem', fontFamily: 'monospace', fontSize: '0.75rem' }}>{w.id.slice(0, 8)}…</td>
            <td style={{ padding: '0.25rem 0.5rem' }}>{w.capabilities.join(', ') || '—'}</td>
            <td style={{ padding: '0.25rem 0.5rem' }}>
              <span className={w.status === 'busy' ? 'log-level-warning' : 'log-level-debug'}>{w.status}</span>
            </td>
            <td style={{ padding: '0.25rem 0.5rem', fontFamily: 'monospace', fontSize: '0.75rem' }}>
              {w.current_execution_id ? w.current_execution_id.slice(0, 8) + '…' : '—'}
            </td>
            <td style={{ padding: '0.25rem 0.5rem' }}>{formatConnectedAt(w.connected_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function WorkersPage() {
  const { data: workerStatus, isLoading } = useWorkerStatus()
  const { data: connectedWorkers = [] } = useConnectedWorkers()
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

      {/* Connected workers table */}
      <div className="worker-card-grid" style={{ marginBottom: '1.5rem' }}>
        <div className="worker-card">
          <div className="worker-card-header">
            <span className="worker-card-title">Connected Workers</span>
            <span className="worker-status-text">{connectedWorkers.length} connected</span>
          </div>
          <ConnectedWorkersTable workers={connectedWorkers} />
        </div>
      </div>

      <div className="worker-card-grid">
        <div className="worker-card">
          {/* Header row */}
          <div className="worker-card-header">
            <span className="worker-card-title">Local Worker Subprocess</span>
            <span className={statusDotClass(status)} />
            <span className="worker-status-text">{status}</span>
            {workerStatus?.uptime_seconds != null && (
              <span className="worker-uptime">up {formatUptime(workerStatus.uptime_seconds)}</span>
            )}
            {workerStatus?.pid != null && (
              <span className="worker-uptime">pid {workerStatus.pid}</span>
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
