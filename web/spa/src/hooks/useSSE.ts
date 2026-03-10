import { useEffect, useRef } from 'react'

export function useSSE(handler: (event: Record<string, unknown>) => void): void {
  const handlerRef = useRef(handler)
  handlerRef.current = handler
  const retryDelay = useRef(1000)

  useEffect(() => {
    let es: EventSource
    let timeoutId: ReturnType<typeof setTimeout>

    function connect() {
      es = new EventSource('/api/events')
      es.onopen = () => {
        retryDelay.current = 1000
      }
      es.addEventListener('task_updated', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data) as Record<string, unknown>
          handlerRef.current(data)
        } catch {
          handlerRef.current({})
        }
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
  }, [])
}
