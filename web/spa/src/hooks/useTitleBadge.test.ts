import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useTitleBadge } from './useTitleBadge'
import * as useBoardModule from './useBoard'
import type { BoardData } from './useBoard'

vi.mock('./useBoard')

function makeBoardData(waitingCount: number): { data: BoardData } {
  const tasks = Array.from({ length: waitingCount }, (_, i) => ({
    id: `task-${i}`,
    title: `Task ${i}`,
    project_name: 'proj',
    project_id: 'p1',
    status: 'waiting_for_input',
    updated_at: null,
    has_spec: false,
    refinement_count: 0,
    unmet_deps: [],
    baseline_qa_failure: null,
  }))

  return {
    data: {
      columns: [
        { status: 'waiting_for_input', label: 'Waiting for Input', tasks },
        { status: 'in_progress', label: 'In Progress', tasks: [] },
      ],
    },
  }
}

describe('useTitleBadge', () => {
  beforeEach(() => {
    document.title = 'Ratchet'
    vi.clearAllMocks()
  })

  afterEach(() => {
    document.title = 'Ratchet'
  })

  it('should set document.title to (1) Ratchet when one waiting_for_input task', () => {
    vi.mocked(useBoardModule.useBoard).mockReturnValue(makeBoardData(1) as ReturnType<typeof useBoardModule.useBoard>)
    renderHook(() => useTitleBadge())
    expect(document.title).toBe('(1) Ratchet')
  })

  it('should set document.title to Ratchet when no waiting_for_input tasks', () => {
    vi.mocked(useBoardModule.useBoard).mockReturnValue(makeBoardData(0) as ReturnType<typeof useBoardModule.useBoard>)
    renderHook(() => useTitleBadge())
    expect(document.title).toBe('Ratchet')
  })

  it('should reset title to Ratchet on unmount', () => {
    vi.mocked(useBoardModule.useBoard).mockReturnValue(makeBoardData(2) as ReturnType<typeof useBoardModule.useBoard>)
    const { unmount } = renderHook(() => useTitleBadge())
    expect(document.title).toBe('(2) Ratchet')
    act(() => unmount())
    expect(document.title).toBe('Ratchet')
  })
})
