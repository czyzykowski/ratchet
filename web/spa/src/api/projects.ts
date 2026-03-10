import { apiFetch } from './client'

export interface Project {
  id: string
  name: string
  repo_url: string
  local_path: string
  status: string
  created_at: string
  updated_at: string
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

export async function fetchProjects(): Promise<Project[]> {
  const data = await apiFetch<{ projects: Project[] }>('/api/projects')
  return data.projects
}

export async function fetchProject(id: string): Promise<{ project: Project; tasks: Task[] }> {
  return apiFetch(`/api/projects/${id}`)
}

export async function createProject(name: string, path: string): Promise<Project> {
  const data = await apiFetch<{ project: Project }>('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, path }),
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
