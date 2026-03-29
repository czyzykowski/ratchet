import { apiFetch } from './client'

export interface PromptTemplate {
  category: string
  name: string
  description: string
  source: string
  template: string
}

export async function fetchPrompts(): Promise<{ data: PromptTemplate[] }> {
  return apiFetch<{ data: PromptTemplate[] }>('/api/settings/prompts')
}
