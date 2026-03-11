import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Notifications } from './Notifications'
import { NotificationsContext } from '../hooks/useNotifications'
import type { Notification } from '../hooks/useNotifications'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

function renderWithContext(notifications: Notification[], dismissNotification = vi.fn()) {
  return render(
    <MemoryRouter>
      <NotificationsContext.Provider
        value={{ notifications, addNotification: vi.fn(), dismissNotification }}
      >
        <Notifications />
      </NotificationsContext.Provider>
    </MemoryRouter>
  )
}

describe('Notifications', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('should render toast with task title', () => {
    const notifications: Notification[] = [
      { id: 'n1', message: 'Task needs your input', taskId: 'task-1', taskTitle: 'My Task' },
    ]
    renderWithContext(notifications)
    expect(screen.getByText('My Task')).toBeInTheDocument()
    expect(screen.getByText('Task needs your input')).toBeInTheDocument()
  })

  it('should call dismissNotification when close button is clicked', () => {
    const dismiss = vi.fn()
    const notifications: Notification[] = [
      { id: 'n1', message: 'Task needs your input', taskId: 'task-1', taskTitle: 'My Task' },
    ]
    renderWithContext(notifications, dismiss)
    fireEvent.click(screen.getByLabelText('Dismiss'))
    expect(dismiss).toHaveBeenCalledWith('n1')
  })

  it('should call navigate when toast body is clicked', () => {
    const dismiss = vi.fn()
    const notifications: Notification[] = [
      { id: 'n1', message: 'Task needs your input', taskId: 'task-1', taskTitle: 'My Task' },
    ]
    renderWithContext(notifications, dismiss)
    fireEvent.click(screen.getByText('My Task'))
    expect(mockNavigate).toHaveBeenCalledWith('/tasks/task-1')
    expect(dismiss).toHaveBeenCalledWith('n1')
  })

  it('should auto-dismiss after 5 seconds', () => {
    vi.useFakeTimers()
    const dismiss = vi.fn()
    const notifications: Notification[] = [
      { id: 'n1', message: 'Task needs your input', taskId: 'task-1', taskTitle: 'My Task' },
    ]
    renderWithContext(notifications, dismiss)
    expect(dismiss).not.toHaveBeenCalled()
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(dismiss).toHaveBeenCalledWith('n1')
  })
})
