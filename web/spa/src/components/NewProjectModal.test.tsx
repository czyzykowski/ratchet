import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { NewProjectModal } from './NewProjectModal'
import * as projectsApi from '../api/projects'

vi.mock('../api/projects', () => ({
  fetchProjects: vi.fn(),
  createProject: vi.fn(),
  fetchProject: vi.fn(),
  updateProject: vi.fn(),
  createTask: vi.fn(),
}))

vi.mock('./BootstrapChat', () => ({
  BootstrapChat: ({ onBack }: { onBack: () => void; onClose: () => void }) => (
    <div data-testid="bootstrap-chat">
      <button onClick={onBack}>Back from bootstrap</button>
    </div>
  ),
}))

function renderModal(open = true, onClose = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return {
    onClose,
    ...render(
      <QueryClientProvider client={queryClient}>
        <NewProjectModal open={open} onClose={onClose} />
      </QueryClientProvider>
    ),
  }
}

describe('NewProjectModal', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.createProject).mockResolvedValue({
      id: '11111111-1111-1111-1111-111111111111',
      name: 'Test',
      repo_url: '/tmp/test',
      local_path: '/tmp/test',
      status: 'active',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
      config_source: 'disk',
      claude_md: null,
      intent_md: null,
      ratchet_yaml: null,
      required_capabilities: [],
    })
  })

  it('should render selection screen with two options when opened', () => {
    renderModal()
    expect(screen.getByText('Manual Setup')).toBeInTheDocument()
    expect(screen.getByText('Bootstrap with AI')).toBeInTheDocument()
  })

  it('should show the project form after clicking Manual Setup', async () => {
    renderModal()
    await userEvent.click(screen.getByText('Manual Setup'))
    expect(screen.getByLabelText(/name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/path/i)).toBeInTheDocument()
  })

  it('should render BootstrapChat after clicking Bootstrap with AI', async () => {
    renderModal()
    await userEvent.click(screen.getByText('Bootstrap with AI'))
    expect(screen.getByTestId('bootstrap-chat')).toBeInTheDocument()
  })

  it('should return to selection screen when back is clicked from manual mode', async () => {
    renderModal()
    await userEvent.click(screen.getByText('Manual Setup'))
    expect(screen.getByLabelText(/name/i)).toBeInTheDocument()
    await userEvent.click(screen.getByText('← Back'))
    expect(screen.getByText('Manual Setup')).toBeInTheDocument()
    expect(screen.getByText('Bootstrap with AI')).toBeInTheDocument()
    expect(screen.queryByLabelText(/name/i)).not.toBeInTheDocument()
  })

  it('should reset to selection screen when modal is reopened', async () => {
    const onClose = vi.fn()
    const { rerender, unmount } = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <NewProjectModal open={true} onClose={onClose} />
      </QueryClientProvider>
    )

    await userEvent.click(screen.getByText('Manual Setup'))
    expect(screen.getByLabelText(/name/i)).toBeInTheDocument()

    // Close and reopen
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <NewProjectModal open={false} onClose={onClose} />
      </QueryClientProvider>
    )
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <NewProjectModal open={true} onClose={onClose} />
      </QueryClientProvider>
    )

    expect(screen.getByText('Manual Setup')).toBeInTheDocument()
    expect(screen.queryByLabelText(/name/i)).not.toBeInTheDocument()
    unmount()
  })
})
