import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { FocusPage } from './FocusPage'
import * as useBoardModule from '../hooks/useBoard'
import * as useActivityModule from '../hooks/useActivity'
import type { BoardData } from '../hooks/useBoard'
import type { ActivityEvent } from '../hooks/useActivity'

vi.mock('../hooks/useBoard', async (importOriginal) => {
  const actual = await importOriginal<typeof useBoardModule>()
  return { ...actual, useBoard: vi.fn() }
})
vi.mock('../hooks/useActivity', async (importOriginal) => {
  const actual = await importOriginal<typeof useActivityModule>()
  return { ...actual, useActivity: vi.fn() }
})
vi.mock('../hooks/useSSE', () => ({ useSSE: vi.fn() }))

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

function makeBoardResult(data: BoardData) {
  return { data, isLoading: false, error: null } as unknown as ReturnType<typeof useBoardModule.useBoard>
}

function makeActivityResult(events: ActivityEvent[]) {
  return { data: { events }, isLoading: false, error: null } as unknown as ReturnType<typeof useActivityModule.useActivity>
}

const boardWithSpecQa: BoardData = {
  columns: [
    { status: 'ready_for_spec', label: 'Ready for Spec', tasks: [] },
    { status: 'spec_qa', label: 'Spec QA', tasks: [] },
    { status: 'in_progress', label: 'In Progress', tasks: [] },
  ],
}

describe('FocusPage', () => {
  beforeEach(() => {
    vi.mocked(useActivityModule.useActivity).mockReturnValue(makeActivityResult([]))
  })

  it('should not render spec_qa pipeline stage when board data contains one', () => {
    vi.mocked(useBoardModule.useBoard).mockReturnValue(makeBoardResult(boardWithSpecQa))
    renderWithProviders(<FocusPage />)
    expect(screen.queryByText(/Spec QA/i)).not.toBeInTheDocument()
  })

  it('should render other pipeline stages normally', () => {
    vi.mocked(useBoardModule.useBoard).mockReturnValue(makeBoardResult(boardWithSpecQa))
    renderWithProviders(<FocusPage />)
    expect(screen.getByText(/Ready for Spec/i)).toBeInTheDocument()
    expect(screen.getByText(/In Progress/i)).toBeInTheDocument()
  })
})
