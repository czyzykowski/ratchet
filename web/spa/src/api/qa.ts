import { apiFetch } from './client'

export interface QAExchange {
  question_index: number
  question: string
  answer: string | null
  execution_id: string
  asked_at: string
  answered_at: string | null
  answered_by: string | null
}

export function fetchTaskQA(taskId: string): Promise<{ history: QAExchange[]; pending: QAExchange | null }> {
  return apiFetch(`/api/tasks/${taskId}/qa`)
}

export function submitAnswer(
  taskId: string,
  answer: string,
  questionIndex: number,
): Promise<{ exchange: QAExchange }> {
  return apiFetch(`/api/tasks/${taskId}/answer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answer, question_index: questionIndex }),
  })
}
