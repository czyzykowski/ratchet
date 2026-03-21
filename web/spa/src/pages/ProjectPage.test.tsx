import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ProjectPage } from './ProjectPage'
import * as projectsApi from '../api/projects'

vi.mock('../api/projects', () => ({
  fetchProjects: vi.fn(),
  createProject: vi.fn(),
  fetchProject: vi.fn(),
  updateProject: vi.fn(),
  createTask: vi.fn(),
}))

vi.mock('../components/ProjectSettingsModal', () => ({
  ProjectSettingsModal: ({ open }: { open: boolean }) =>
    open ? <div data-testid="project-settings-modal">Settings Modal</div> : null,
}))

vi.mock('../hooks/useSSE', () => ({
  useSSE: vi.fn(),
}))

vi.mock('../components/TaskDetailModal', () => ({
  TaskDetailModal: ({ taskId }: { taskId: string | null }) =>
    taskId ? <div data-testid="task-detail-modal">{taskId}</div> : null,
}))

function renderWithProviders(ui: React.ReactElement, initialPath = '/projects/test-id') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/projects/:project_id" element={ui} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    ),
  }
}

const projectId = '22222222-2222-2222-2222-222222222222'

const mockData = {
  project: {
    id: projectId,
    name: 'Test Project',
    repo_url: '/tmp/test',
    local_path: '/tmp/test',
    status: 'active',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-02T00:00:00Z',
    config_source: 'disk',
    claude_md: null,
    intent_md: null,
    ratchet_yaml: null,
    required_capabilities: [],
  },
  tasks: [
    {
      id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      project_id: projectId,
      title: 'Task Alpha',
      status: 'ready_for_spec',
      refinement_count: 0,
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-02T00:00:00Z',
    },
    {
      id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
      project_id: projectId,
      title: 'Task Beta',
      status: 'in_progress',
      refinement_count: 1,
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-03T00:00:00Z',
    },
  ],
}

describe('ProjectPage', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.fetchProject).mockResolvedValue(mockData)
  })

  it('should render project name and task rows', async () => {
    renderWithProviders(<ProjectPage />, `/projects/${projectId}`)
    expect(await screen.findByRole('heading', { name: 'Test Project' })).toBeInTheDocument()
    expect(screen.getByText('Task Alpha')).toBeInTheDocument()
    expect(screen.getByText('Task Beta')).toBeInTheDocument()
  })

  it('should open TaskDetailModal when task row clicked', async () => {
    renderWithProviders(<ProjectPage />, `/projects/${projectId}`)
    await screen.findByText('Task Alpha')
    await userEvent.click(screen.getByText('Task Alpha'))
    expect(screen.getByTestId('task-detail-modal')).toBeInTheDocument()
  })

  it('should open NewTaskModal when Add Task clicked', async () => {
    renderWithProviders(<ProjectPage />, `/projects/${projectId}`)
    await screen.findByText('Task Alpha')
    await userEvent.click(screen.getByRole('button', { name: /add task/i }))
    expect(screen.getByLabelText(/title/i)).toBeInTheDocument()
  })

  it('should show capabilities input pre-populated with project defaults when NewTaskModal opens', async () => {
    vi.mocked(projectsApi.fetchProject).mockResolvedValue({
      ...mockData,
      project: { ...mockData.project, required_capabilities: ['linux', 'gpu'] },
    })
    renderWithProviders(<ProjectPage />, `/projects/${projectId}`)
    await screen.findByText('Task Alpha')
    await userEvent.click(screen.getByRole('button', { name: /add task/i }))
    const capsInput = screen.getByLabelText(/required capabilities/i)
    expect(capsInput).toBeInTheDocument()
    expect((capsInput as HTMLInputElement).value).toBe('linux, gpu')
  })

  it('should show loading state', () => {
    vi.mocked(projectsApi.fetchProject).mockReturnValue(new Promise(() => {}))
    renderWithProviders(<ProjectPage />, `/projects/${projectId}`)
    expect(screen.getByText(/loading project/i)).toBeInTheDocument()
  })

  it('should render Settings button and open ProjectSettingsModal on click', async () => {
    renderWithProviders(<ProjectPage />, `/projects/${projectId}`)
    await screen.findByText('Test Project')
    expect(screen.queryByTestId('project-settings-modal')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /settings/i }))
    expect(screen.getByTestId('project-settings-modal')).toBeInTheDocument()
  })
})
