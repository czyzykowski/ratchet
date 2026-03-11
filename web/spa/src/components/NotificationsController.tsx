import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSSE } from '../hooks/useSSE'
import { useBoard } from '../hooks/useBoard'
import { useNotifications } from '../hooks/useNotifications'

export function NotificationsController() {
  const queryClient = useQueryClient()
  const { data: board } = useBoard()
  const { addNotification } = useNotifications()
  const prevWaitingIds = useRef<Set<string>>(new Set())
  const hasLoaded = useRef(false)

  useSSE(() => {
    queryClient.invalidateQueries({ queryKey: ['board'] })
  })

  useEffect(() => {
    if (!board) return

    const currentWaiting = new Map<string, string>()
    for (const col of board.columns) {
      if (col.status === 'waiting_for_input') {
        for (const task of col.tasks) {
          currentWaiting.set(task.id, task.title)
        }
      }
    }

    if (hasLoaded.current) {
      for (const [taskId, taskTitle] of currentWaiting.entries()) {
        if (!prevWaitingIds.current.has(taskId)) {
          addNotification({ message: 'Task needs your input', taskId, taskTitle })
        }
      }
    }

    hasLoaded.current = true
    prevWaitingIds.current = new Set(currentWaiting.keys())
  }, [board, addNotification])

  return null
}
