import { apiFetch } from './client'

export interface BootstrapSessionEntry {
  session_id: string
  created_at: string
}

export async function createBootstrapSession(workingDirectory?: string): Promise<{ session_id: string }> {
  const body: Record<string, unknown> = {}
  if (workingDirectory !== undefined) {
    body.working_directory = workingDirectory
  }
  return apiFetch('/api/bootstrap-chat-sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function sendBootstrapMessage(sessionId: string, userInput: string): Promise<Response> {
  return fetch(`/api/bootstrap-chat-sessions/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_input: userInput }),
  })
}
