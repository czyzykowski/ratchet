import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export function useSSE(url: string) {
  const queryClient = useQueryClient()
  const retryDelay = useRef(1000)

  useEffect(() => {
    let es: EventSource
    let timeoutId: ReturnType<typeof setTimeout>

    function connect() {
      es = new EventSource(url)
      es.onopen = () => {
        retryDelay.current = 1000
      }
      es.addEventListener('task_updated', () => {
        queryClient.invalidateQueries({ queryKey: ['board'] })
      })
      es.onerror = () => {
        es.close()
        timeoutId = setTimeout(() => {
          retryDelay.current = Math.min(retryDelay.current * 2, 30_000)
          connect()
        }, retryDelay.current)
      }
    }

    connect()
    return () => {
      es.close()
      clearTimeout(timeoutId)
    }
  }, [url, queryClient])
}
