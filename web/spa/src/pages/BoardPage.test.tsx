import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { BoardPage } from './BoardPage'
import * as useBoardModule from '../hooks/useBoard'
import type { BoardData } from '../hooks/useBoard'

vi.mock('../hooks/useBoard', async (importOriginal) => {
  const actual = await importOriginal<typeof useBoardModule>()
  return { ...actual, useBoard: vi.fn() }
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

const boardWithSpecQa: BoardData = {
  columns: [
    { status: 'ready_for_spec', label: 'Ready for Spec', tasks: [] },
    { status: 'spec_qa', label: 'SPEC QA', tasks: [] },
    { status: 'ready_for_implementation', label: 'Ready for Impl', tasks: [] },
  ],
}

describe('BoardPage', () => {
  beforeEach(() => {
    vi.mocked(useBoardModule.useBoard).mockReturnValue(makeBoardResult(boardWithSpecQa))
  })

  it('should not render spec_qa column when board data contains one', () => {
    renderWithProviders(<BoardPage />)
    expect(screen.queryByText(/SPEC QA/i)).not.toBeInTheDocument()
  })

  it('should render other columns normally', () => {
    renderWithProviders(<BoardPage />)
    expect(screen.getByText(/Ready for Spec/i)).toBeInTheDocument()
    expect(screen.getByText(/Ready for Impl/i)).toBeInTheDocument()
  })
})
