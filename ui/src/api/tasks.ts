import type { TaskDetailResponse } from './types'

export function fetchTask(id: string): Promise<TaskDetailResponse> {
  return fetch(`/api/tasks/${id}`).then((r) => r.json())
}
