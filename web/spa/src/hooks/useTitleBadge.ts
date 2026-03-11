import { useEffect } from 'react'
import { useBoard } from './useBoard'

export function useTitleBadge(): void {
  const { data: board } = useBoard()

  useEffect(() => {
    const count =
      board?.columns.reduce(
        (acc, col) => acc + (col.status === 'waiting_for_input' ? col.tasks.length : 0),
        0,
      ) ?? 0

    document.title = count > 0 ? `(${count}) Ratchet` : 'Ratchet'

    return () => {
      document.title = 'Ratchet'
    }
  }, [board])
}
