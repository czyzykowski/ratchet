import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { TaskDetailModal } from './TaskDetailModal'
import * as qaApi from '../api/qa'

vi.mock('../api/qa', () => ({
  fetchTaskQA: vi.fn(),
  submitAnswer: vi.fn(),
}))

vi.mock('./Markdown', () => ({
  Markdown: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div>,
}))

vi.mock('./CreateSpecChat', () => ({
  CreateSpecChat: () => <div>CreateSpecChat</div>,
}))

const taskId = 'task-111'

function makeTaskResponse(status: string) {
  return {
    task: {
      id: taskId,
      title: 'Test Task',
      status,
      project_id: 'proj-1',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: null,
      current_spec_id: null,
      refinement_count: 0,
      depends_on: [],
    },
    project_name: 'Test Project',
    specs: [],
    executions: [],
    dependencies: [],
    qa_failure: null,
    baseline_qa_failure: null,
  }
}

function renderModal(taskResponse: ReturnType<typeof makeTaskResponse>) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(taskResponse),
  }))

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TaskDetailModal taskId={taskId} onClose={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('TaskDetailModal Q&A panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('should not show Q&A panel when status is not waiting_for_input', async () => {
    vi.mocked(qaApi.fetchTaskQA).mockResolvedValue({ history: [], pending: null })
    renderModal(makeTaskResponse('blocked'))
    await screen.findByText('Test Task')
    expect(screen.queryByText('Question')).not.toBeInTheDocument()
  })

  it('should show Q&A panel with pending question when status is waiting_for_input', async () => {
    vi.mocked(qaApi.fetchTaskQA).mockResolvedValue({
      history: [],
      pending: {
        question_index: 0,
        question: 'What should I do?',
        answer: null,
        execution_id: 'exec-1',
        asked_at: '2024-01-01T00:00:00Z',
        answered_at: null,
        answered_by: null,
      },
    })
    renderModal(makeTaskResponse('waiting_for_input'))
    await screen.findByText('Test Task')
    await screen.findByText('What should I do?')
    expect(screen.getByText('Question')).toBeInTheDocument()
  })

  it('should call submitAnswer with correct args when form is submitted', async () => {
    vi.mocked(qaApi.fetchTaskQA).mockResolvedValue({
      history: [],
      pending: {
        question_index: 2,
        question: 'What should I do?',
        answer: null,
        execution_id: 'exec-1',
        asked_at: '2024-01-01T00:00:00Z',
        answered_at: null,
        answered_by: null,
      },
    })
    vi.mocked(qaApi.submitAnswer).mockResolvedValue({
      exchange: {
        question_index: 2,
        question: 'What should I do?',
        answer: 'My answer',
        execution_id: 'exec-1',
        asked_at: '2024-01-01T00:00:00Z',
        answered_at: '2024-01-01T01:00:00Z',
        answered_by: 'user',
      },
    })

    renderModal(makeTaskResponse('waiting_for_input'))
    await screen.findByText('What should I do?')

    const textarea = screen.getByPlaceholderText('Your answer…')
    fireEvent.change(textarea, { target: { value: 'My answer' } })

    const btn = screen.getByRole('button', { name: 'Submit Answer' })
    fireEvent.click(btn)

    await waitFor(() => {
      expect(qaApi.submitAnswer).toHaveBeenCalledWith(taskId, 'My answer', 2)
    })
  })
})
