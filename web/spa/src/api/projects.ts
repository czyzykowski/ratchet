import { apiFetch } from './client'

export interface Project {
  id: string
  name: string
  repo_url: string
  local_path: string
  status: string
  created_at: string
  updated_at: string
  config_source: string
  claude_md: string | null
  intent_md: string | null
  ratchet_yaml: string | null
  required_capabilities: string[]
}

export interface Task {
  id: string
  project_id: string
  title: string
  status: string
  refinement_count: number
  created_at: string
  updated_at: string
}

export interface UpdateProjectBody {
  name: string
  repo_url: string
  local_path: string
  config_source: string
  claude_md?: string | null
  intent_md?: string | null
  ratchet_yaml?: string | null
  required_capabilities?: string[]
}

export async function fetchProjects(): Promise<Project[]> {
  const data = await apiFetch<{ projects: Project[] }>('/api/projects')
  return data.projects
}

export async function fetchProject(id: string): Promise<{ project: Project; tasks: Task[] }> {
  return apiFetch(`/api/projects/${id}`)
}

export async function createProject(
  name: string,
  path: string,
  options?: {
    config_source?: string
    repo_url?: string
    claude_md?: string | null
    intent_md?: string | null
    ratchet_yaml?: string | null
    required_capabilities?: string[]
  }
): Promise<Project> {
  const data = await apiFetch<{ project: Project }>('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, path, ...options }),
  })
  return data.project
}

export async function updateProject(id: string, body: UpdateProjectBody): Promise<Project> {
  const data = await apiFetch<{ project: Project }>(`/api/projects/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return data.project
}

export async function createTask(projectId: string, title: string): Promise<Task> {
  const data = await apiFetch<{ task: Task }>('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: projectId, title }),
  })
  return data.task
}
