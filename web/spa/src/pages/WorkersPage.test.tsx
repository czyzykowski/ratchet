import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { WorkersPage } from './WorkersPage'
import * as useWorkerStatusModule from '../hooks/useWorkerStatus'
import * as useWorkerLogsModule from '../hooks/useWorkerLogs'

vi.mock('../hooks/useWorkerStatus', async (importOriginal) => {
  const actual = await importOriginal<typeof useWorkerStatusModule>()
  return {
    ...actual,
    useWorkerStatus: vi.fn(),
    useStartWorker: vi.fn(),
    useStopWorker: vi.fn(),
    useRestartWorker: vi.fn(),
    useUpdateWorkerSettings: vi.fn(),
  }
})

vi.mock('../hooks/useWorkerLogs', async (importOriginal) => {
  const actual = await importOriginal<typeof useWorkerLogsModule>()
  return {
    ...actual,
    useWorkerLogs: vi.fn(),
  }
})

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const noopMutation = { mutate: vi.fn(), isPending: false } as unknown as any

const defaultSettings = {
  watchdog_timeout: 60,
  max_workers: 2,
  local_capabilities: [],
  enabled: true,
}

function mockRunningStatus() {
  vi.mocked(useWorkerStatusModule.useWorkerStatus).mockReturnValue({
    data: {
      status: 'running',
      started_at: '2026-01-01T00:00:00Z',
      error_message: null,
      settings: defaultSettings,
      uptime_seconds: 3661,
    },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useWorkerStatusModule.useWorkerStatus>)
}

function mockStoppedStatus() {
  vi.mocked(useWorkerStatusModule.useWorkerStatus).mockReturnValue({
    data: {
      status: 'stopped',
      started_at: null,
      error_message: null,
      settings: defaultSettings,
      uptime_seconds: null,
    },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useWorkerStatusModule.useWorkerStatus>)
}

function mockErrorStatus() {
  vi.mocked(useWorkerStatusModule.useWorkerStatus).mockReturnValue({
    data: {
      status: 'error',
      started_at: null,
      error_message: 'Worker crashed unexpectedly',
      settings: defaultSettings,
      uptime_seconds: null,
    },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useWorkerStatusModule.useWorkerStatus>)
}

function setupMocks() {
  vi.mocked(useWorkerStatusModule.useStartWorker).mockReturnValue(noopMutation)
  vi.mocked(useWorkerStatusModule.useStopWorker).mockReturnValue(noopMutation)
  vi.mocked(useWorkerStatusModule.useRestartWorker).mockReturnValue(noopMutation)
  vi.mocked(useWorkerStatusModule.useUpdateWorkerSettings).mockReturnValue(noopMutation)
  vi.mocked(useWorkerLogsModule.useWorkerLogs).mockReturnValue({ logs: [], connected: false })
}

describe('WorkersPage', () => {
  it('should render "Embedded Worker" card title', () => {
    setupMocks()
    mockRunningStatus()
    renderWithProviders(<WorkersPage />)
    expect(screen.getByText('Embedded Worker')).toBeInTheDocument()
  })

  it('should show green status dot and "running" text when status is running', () => {
    setupMocks()
    mockRunningStatus()
    renderWithProviders(<WorkersPage />)
    expect(screen.getByText('running')).toBeInTheDocument()
    const dot = document.querySelector('.dot-running')
    expect(dot).toBeInTheDocument()
  })

  it('should show red status dot and error message when status is error', () => {
    setupMocks()
    mockErrorStatus()
    renderWithProviders(<WorkersPage />)
    expect(screen.getByText('error')).toBeInTheDocument()
    const dot = document.querySelector('.dot-error')
    expect(dot).toBeInTheDocument()
    expect(screen.getByText('Worker crashed unexpectedly')).toBeInTheDocument()
  })

  it('should disable Start button when status is running', () => {
    setupMocks()
    mockRunningStatus()
    renderWithProviders(<WorkersPage />)
    const startBtn = screen.getByRole('button', { name: 'Start' })
    expect(startBtn).toBeDisabled()
  })

  it('should disable Stop button when status is stopped', () => {
    setupMocks()
    mockStoppedStatus()
    renderWithProviders(<WorkersPage />)
    const stopBtn = screen.getByRole('button', { name: /stop/i })
    expect(stopBtn).toBeDisabled()
  })

  it('should render log entries with timestamp and level', () => {
    setupMocks()
    mockRunningStatus()
    vi.mocked(useWorkerLogsModule.useWorkerLogs).mockReturnValue({
      logs: [
        { timestamp: '2026-01-01T12:00:00Z', level: 'INFO', message: 'Worker started' },
        { timestamp: '2026-01-01T12:00:01Z', level: 'ERROR', message: 'Something failed' },
      ],
      connected: true,
    })
    renderWithProviders(<WorkersPage />)
    expect(screen.getByText(/Worker started/)).toBeInTheDocument()
    expect(screen.getByText(/Something failed/)).toBeInTheDocument()
    expect(screen.getByText(/INFO/)).toBeInTheDocument()
    expect(screen.getByText(/ERROR/)).toBeInTheDocument()
  })

  it('should show loading state when status query is loading', () => {
    setupMocks()
    vi.mocked(useWorkerStatusModule.useWorkerStatus).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as unknown as ReturnType<typeof useWorkerStatusModule.useWorkerStatus>)
    renderWithProviders(<WorkersPage />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('should render settings panel with current values when settings opened', () => {
    setupMocks()
    mockStoppedStatus()
    renderWithProviders(<WorkersPage />)
    const settingsBtn = screen.getByRole('button', { name: /settings/i })
    fireEvent.click(settingsBtn)
    expect(screen.getByDisplayValue('60')).toBeInTheDocument()
    expect(screen.getByDisplayValue('2')).toBeInTheDocument()
  })
})
