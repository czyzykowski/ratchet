import { apiFetch } from './client'

export interface SessionEntry {
  session_id: string
  created_at: string
}

export interface ChatMessage {
  content: string
  assistant: string
  image_id?: string | null
}

export async function createProjectChatSession(projectId: string): Promise<{ session_id: string }> {
  return apiFetch('/api/project-chat-sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: projectId }),
  })
}

export async function listProjectChatSessions(projectId: string): Promise<SessionEntry[]> {
  const data = await apiFetch<{ sessions: SessionEntry[] }>(
    `/api/project-chat-sessions?project_id=${projectId}`
  )
  return data.sessions
}

export async function getProjectChatSession(sessionId: string): Promise<{ messages: ChatMessage[] }> {
  return apiFetch(`/api/project-chat-sessions/${sessionId}`)
}

export async function sendProjectChatMessage(
  sessionId: string,
  userInput: string,
  imageId?: string
): Promise<Response> {
  const body: Record<string, unknown> = { user_input: userInput }
  if (imageId !== undefined) {
    body.image_id = imageId
  }
  return fetch(`/api/project-chat-sessions/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function deleteProjectChatSession(sessionId: string): Promise<void> {
  await apiFetch(`/api/project-chat-sessions/${sessionId}`, { method: 'DELETE' })
}
