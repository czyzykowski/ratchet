import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ProjectsPage } from './ProjectsPage'
import * as projectsApi from '../api/projects'

vi.mock('../api/projects', () => ({
  fetchProjects: vi.fn(),
  createProject: vi.fn(),
  fetchProject: vi.fn(),
  updateProject: vi.fn(),
  createTask: vi.fn(),
}))

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        {ui}
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const mockProject = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'My Project',
  repo_url: '/tmp/my-project',
  local_path: '/tmp/my-project',
  status: 'active',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-02T00:00:00Z',
  config_source: 'disk',
  claude_md: null,
  intent_md: null,
  ratchet_yaml: null,
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.fetchProjects).mockResolvedValue([mockProject])
  })

  it('should render project rows from fetchProjects', async () => {
    renderWithProviders(<ProjectsPage />)
    expect(await screen.findByText('My Project')).toBeInTheDocument()
    expect(screen.getByText('/tmp/my-project')).toBeInTheDocument()
  })

  it('should open NewProjectModal when New Project button clicked', async () => {
    renderWithProviders(<ProjectsPage />)
    await screen.findByText('My Project')
    await userEvent.click(screen.getByRole('button', { name: /new project/i }))
    expect(screen.getByLabelText(/name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/path/i)).toBeInTheDocument()
  })

  it('should show loading state', () => {
    vi.mocked(projectsApi.fetchProjects).mockReturnValue(new Promise(() => {}))
    renderWithProviders(<ProjectsPage />)
    expect(screen.getByText(/loading projects/i)).toBeInTheDocument()
  })
})
