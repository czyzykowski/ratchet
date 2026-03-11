import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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

const featureId = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'

const mockData = {
  feature: {
    id: featureId,
    project_id: 'proj-1',
    title: 'My Feature',
    description: '## Feature Description\n\nThis is the feature.',
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
    },
  ],
}

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
    expect(await screen.findByRole('heading', { name: 'My Feature' })).toBeInTheDocument()
  })

  it('should render description with Markdown', async () => {
    renderWithProviders()
    await screen.findByRole('heading', { name: 'My Feature' })
    const markdownEls = screen.getAllByTestId('markdown')
    expect(markdownEls[0].textContent).toContain('## Feature Description')
  })

  it('should render high-level spec content with Markdown', async () => {
    renderWithProviders()
    await screen.findByRole('heading', { name: 'My Feature' })
    const markdownEls = screen.getAllByTestId('markdown')
    expect(markdownEls.some(el => el.textContent?.includes('## High Level Spec'))).toBe(true)
  })
})
