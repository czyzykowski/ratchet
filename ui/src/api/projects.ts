import type { Project, ProjectDetailResponse } from './types'

export function fetchProjects(): Promise<{ projects: Project[] }> {
  return fetch('/api/projects').then((r) => r.json())
}

export function fetchProject(id: string): Promise<ProjectDetailResponse> {
  return fetch(`/api/projects/${id}`).then((r) => r.json())
}
