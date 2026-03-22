import { apiFetch } from './client'

export interface WorkerSettings {
  watchdog_timeout: number
  max_workers: number
  local_capabilities: string[]
  enabled: boolean
}

export interface WorkerStatus {
  status: string
  started_at: string | null
  error_message: string | null
  settings: WorkerSettings
  uptime_seconds: number | null
  pid: number | null
}

export interface LogEntry {
  timestamp: string
  level: string
  message: string
}

export function fetchWorkerStatus(): Promise<WorkerStatus> {
  return apiFetch<WorkerStatus>('/api/worker/status')
}

export function startWorker(): Promise<WorkerStatus> {
  return apiFetch<WorkerStatus>('/api/worker/start', { method: 'POST' })
}

export function stopWorker(graceful = true): Promise<WorkerStatus> {
  return apiFetch<WorkerStatus>('/api/worker/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graceful }),
  })
}

export function restartWorker(graceful = true): Promise<WorkerStatus> {
  return apiFetch<WorkerStatus>('/api/worker/restart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graceful }),
  })
}

export function updateWorkerSettings(settings: Partial<WorkerSettings>): Promise<WorkerSettings> {
  return apiFetch<WorkerSettings>('/api/worker/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
}
