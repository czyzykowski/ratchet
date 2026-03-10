import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchBoard } from '../api/board'
import type { BoardResponse } from '../api/types'

export function useBoard(): { data: BoardResponse | undefined; isLoading: boolean; error: unknown } {
  const queryClient = useQueryClient()

  const { data, isLoading, error } = useQuery({
    queryKey: ['board'],
    queryFn: fetchBoard,
  })

  useEffect(() => {
    const handler = (): void => {
      void queryClient.invalidateQueries({ queryKey: ['board'] })
    }
    window.addEventListener('task_updated', handler)
    return () => {
      window.removeEventListener('task_updated', handler)
    }
  }, [queryClient])

  return { data, isLoading, error }
}
