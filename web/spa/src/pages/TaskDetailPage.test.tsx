import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { TaskDetailPage } from './TaskDetailPage'
import * as client from '../api/client'

vi.mock('../api/client', () => ({
  apiFetch: vi.fn(),
}))

vi.mock('../components/Markdown', () => ({
  Markdown: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div>,
}))

const taskId = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
const specId = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
const executionId = 'cccccccc-cccc-cccc-cccc-cccccccccccc'

const mockData = {
  task: {
    id: taskId,
    title: 'My Task Title',
    status: 'ready_for_implementation',
    project_id: 'proj-1',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-02T00:00:00Z',
    current_spec_id: specId,
    refinement_count: 1,
    depends_on: [],
  },
  project_name: 'Test Project',
  specs: [
    {
      id: specId,
      content: '# Spec Heading\n\nDo the thing.',
      created_at: '2024-01-01T00:00:00Z',
    },
  ],
  executions: [
    {
      id: executionId,
      status: 'completed',
      failure_reason: null,
      branch_name: 'execution/123',
      started_at: '2024-01-02T00:00:00Z',
      completed_at: '2024-01-02T01:00:00Z',
    },
  ],
  dependencies: [],
  qa_failure: null,
  baseline_qa_failure: null,
}

function renderWithProviders(path = `/tasks/${taskId}`) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/tasks/:task_id" element={<TaskDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('TaskDetailPage', () => {
  beforeEach(() => {
    vi.mocked(client.apiFetch).mockResolvedValue(mockData)
  })

  it('should render task title', async () => {
    renderWithProviders()
    expect(await screen.findByRole('heading', { name: 'My Task Title' })).toBeInTheDocument()
  })

  it('should render spec with Markdown', async () => {
    renderWithProviders()
    await screen.findByRole('heading', { name: 'My Task Title' })
    const markdownEl = screen.getByTestId('markdown')
    expect(markdownEl.textContent).toContain('# Spec Heading')
  })

  it('should render execution row', async () => {
    renderWithProviders()
    await screen.findByRole('heading', { name: 'My Task Title' })
    expect(screen.getByText('completed')).toBeInTheDocument()
  })

  it('should render Archive button when task is not abandoned', async () => {
    renderWithProviders()
    await screen.findByRole('heading', { name: 'My Task Title' })
    expect(screen.getByRole('button', { name: 'Archive' })).toBeInTheDocument()
  })

  it('should not render Archive button when task is abandoned', async () => {
    vi.mocked(client.apiFetch).mockResolvedValue({
      ...mockData,
      task: { ...mockData.task, status: 'abandoned' },
    })
    renderWithProviders()
    await screen.findByRole('heading', { name: 'My Task Title' })
    expect(screen.queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
  })
})
