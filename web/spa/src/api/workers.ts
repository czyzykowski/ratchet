import { apiFetch } from './client'

export interface ConnectedWorker {
  id: string
  capabilities: string[]
  current_execution_id: string | null
  connected_at: string
  status: 'idle' | 'busy'
}

export function fetchConnectedWorkers(): Promise<ConnectedWorker[]> {
  return apiFetch<ConnectedWorker[]>('/api/workers')
}
