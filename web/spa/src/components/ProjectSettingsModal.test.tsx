import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ProjectSettingsModal } from './ProjectSettingsModal'
import * as projectsApi from '../api/projects'
import type { Project } from '../api/projects'

vi.mock('../api/projects', () => ({
  fetchProjects: vi.fn(),
  fetchProject: vi.fn(),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  createTask: vi.fn(),
}))

const mockProject: Project = {
  id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
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
}

function renderModal(props: Partial<Parameters<typeof ProjectSettingsModal>[0]> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ProjectSettingsModal
        project={mockProject}
        open={true}
        onClose={vi.fn()}
        {...props}
      />
    </QueryClientProvider>
  )
}

describe('ProjectSettingsModal', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.updateProject).mockResolvedValue({ ...mockProject, name: 'Updated' })
  })

  it('should render pre-populated fields from project data', () => {
    renderModal()
    expect(screen.getByDisplayValue('Test Project')).toBeInTheDocument()
    expect(screen.getAllByDisplayValue('/tmp/test').length).toBeGreaterThanOrEqual(1)
  })

  it('should hide file textareas when config_source is disk', () => {
    renderModal()
    expect(screen.queryByLabelText(/CLAUDE\.md/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/INTENT\.md/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/ratchet\.yaml/i)).not.toBeInTheDocument()
  })

  it('should show file textareas when config_source is db', async () => {
    const dbProject: Project = { ...mockProject, config_source: 'db', claude_md: '# Claude' }
    renderModal({ project: dbProject })
    expect(screen.getByLabelText(/CLAUDE\.md/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/INTENT\.md/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/ratchet\.yaml/i)).toBeInTheDocument()
  })

  it('should call updateProject with correct payload on submit', async () => {
    renderModal()
    const nameInput = screen.getByDisplayValue('Test Project')
    await userEvent.clear(nameInput)
    await userEvent.type(nameInput, 'New Name')
    fireEvent.submit(screen.getByRole('button', { name: /save settings/i }).closest('form')!)
    await waitFor(() => {
      expect(projectsApi.updateProject).toHaveBeenCalledWith(
        mockProject.id,
        expect.objectContaining({ name: 'New Name' })
      )
    })
  })

  it('should display error message when update fails', async () => {
    vi.mocked(projectsApi.updateProject).mockRejectedValueOnce({ message: 'Server error' })
    renderModal()
    fireEvent.submit(screen.getByRole('button', { name: /save settings/i }).closest('form')!)
    await waitFor(() => {
      expect(screen.getByText('Server error')).toBeInTheDocument()
    })
  })
})
