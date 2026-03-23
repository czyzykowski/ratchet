import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { FeatureDetailPage } from './FeatureDetailPage'
import * as client from '../api/client'

vi.mock('../api/client', () => ({
  apiFetch: vi.fn(),
}))

vi.mock('../components/Markdown', () => ({
  Markdown: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div>,
}))

vi.mock('../components/CreateFeatureChat', () => ({
  CreateFeatureChat: () => <div data-testid="create-feature-chat" />,
}))

vi.mock('../components/FeatureProgressBar', () => ({
  FeatureProgressBar: () => <div data-testid="feature-progress-bar" />,
}))

const featureId = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'

function makeData(status: string, abandoned = false) {
  return {
    feature: {
      id: featureId,
      project_id: 'proj-1',
      title: 'My Feature',
      description: '## Feature Description\n\nThis is the feature.',
      session_id: null,
      status,
      abandoned,
    },
    specs: [
      {
        id: 'spec-1',
        feature_id: featureId,
        task_id: null,
        title: 'First Spec',
        order: 1,
        content: '## High Level Spec\n\nDo this.',
        compiled: false,
        dependencies: [],
        task_status: null,
      },
    ],
  }
}

const mockData = makeData('idea')

function renderWithProviders(path = `/features/${featureId}`) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/features/:feature_id" element={<FeatureDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('FeatureDetailPage', () => {
  beforeEach(() => {
    vi.mocked(client.apiFetch).mockResolvedValue(mockData)
  })

  it('should render feature title', async () => {
    renderWithProviders()
    expect(await screen.findByRole('heading', { name: /My Feature/ })).toBeInTheDocument()
  })

  it('should render description with Markdown', async () => {
    renderWithProviders()
    await screen.findByRole('heading', { name: /My Feature/ })
    const markdownEls = screen.getAllByTestId('markdown')
    expect(markdownEls[0].textContent).toContain('## Feature Description')
  })

  it('should render high-level spec content with Markdown', async () => {
    renderWithProviders()
    await screen.findByRole('heading', { name: /My Feature/ })
    const markdownEls = screen.getAllByTestId('markdown')
    expect(markdownEls.some(el => el.textContent?.includes('## High Level Spec'))).toBe(true)
  })
})

describe('FeatureDetailPage — Abandon button visibility', () => {
  it('should show Abandon button for idea status', async () => {
    vi.mocked(client.apiFetch).mockResolvedValue(makeData('idea'))
    renderWithProviders()
    await screen.findByRole('heading', { name: /My Feature/ })
    expect(screen.getByRole('button', { name: 'Abandon' })).toBeInTheDocument()
  })

  it('should show Abandon button for in_clarification status', async () => {
    vi.mocked(client.apiFetch).mockResolvedValue(makeData('in_clarification'))
    renderWithProviders()
    await screen.findByRole('heading', { name: /My Feature/ })
    expect(screen.getByRole('button', { name: 'Abandon' })).toBeInTheDocument()
  })

  it('should not show Abandon button for defined status', async () => {
    vi.mocked(client.apiFetch).mockResolvedValue(makeData('defined'))
    renderWithProviders()
    await screen.findByRole('heading', { name: /My Feature/ })
    expect(screen.queryByRole('button', { name: 'Abandon' })).not.toBeInTheDocument()
  })

  it('should not show Abandon button for in_progress status', async () => {
    vi.mocked(client.apiFetch).mockResolvedValue(makeData('in_progress'))
    renderWithProviders()
    await screen.findByRole('heading', { name: /My Feature/ })
    expect(screen.queryByRole('button', { name: 'Abandon' })).not.toBeInTheDocument()
  })

  it('should not show Abandon button for abandoned status', async () => {
    vi.mocked(client.apiFetch).mockResolvedValue(makeData('abandoned', true))
    renderWithProviders()
    await screen.findByRole('heading', { name: /My Feature/ })
    expect(screen.queryByRole('button', { name: 'Abandon' })).not.toBeInTheDocument()
  })
})

describe('FeatureDetailPage — Abandon form interaction', () => {
  beforeEach(() => {
    vi.mocked(client.apiFetch).mockResolvedValue(makeData('idea'))
  })

  it('should show reason textarea after clicking Abandon', async () => {
    renderWithProviders()
    await screen.findByRole('heading', { name: /My Feature/ })
    fireEvent.click(screen.getByRole('button', { name: 'Abandon' }))
    expect(screen.getByPlaceholderText('Reason for abandonment (optional)')).toBeInTheDocument()
  })

  it('should hide form when Cancel is clicked', async () => {
    renderWithProviders()
    await screen.findByRole('heading', { name: /My Feature/ })
    fireEvent.click(screen.getByRole('button', { name: 'Abandon' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByPlaceholderText('Reason for abandonment (optional)')).not.toBeInTheDocument()
  })

  it('should call API with reason when Confirm Abandon is clicked', async () => {
    // First call returns idea feature, second call (after mutation) also returns for cache invalidation
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(makeData('idea'))   // initial query
      .mockResolvedValueOnce({ feature: { ...makeData('abandoned', true).feature } })  // POST
      .mockResolvedValue(makeData('abandoned', true))  // re-fetch

    renderWithProviders()
    await screen.findByRole('heading', { name: /My Feature/ })

    fireEvent.click(screen.getByRole('button', { name: 'Abandon' }))
    const textarea = screen.getByPlaceholderText('Reason for abandonment (optional)')
    fireEvent.change(textarea, { target: { value: 'Superseded by feature X' } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Abandon' }))

    await waitFor(() => {
      const calls = vi.mocked(client.apiFetch).mock.calls
      const postCall = calls.find(c => c[1]?.method === 'POST')
      expect(postCall).toBeDefined()
      expect(postCall![1]?.body).toContain('Superseded by feature X')
    })
  })
})
