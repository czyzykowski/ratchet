import { apiFetch } from './client'

export interface BootstrapSessionEntry {
  session_id: string
  created_at: string
}

export interface BootstrapMessage {
  role: string
  content: string
  assistant: string | null
  image_id: string | null
}

export interface BootstrapSessionDetail {
  session_id: string
  messages: BootstrapMessage[]
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

export async function listBootstrapSessions(): Promise<BootstrapSessionEntry[]> {
  return apiFetch('/api/bootstrap-chat-sessions')
}

export async function loadBootstrapSession(sessionId: string): Promise<BootstrapSessionDetail> {
  return apiFetch(`/api/bootstrap-chat-sessions/${sessionId}`)
}

export async function sendBootstrapMessageWithImage(
  sessionId: string,
  userInput: string,
  imageId?: string,
): Promise<Response> {
  const body: Record<string, unknown> = { user_input: userInput }
  if (imageId) body.image_id = imageId
  return fetch(`/api/bootstrap-chat-sessions/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}
