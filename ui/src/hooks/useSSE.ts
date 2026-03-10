import { useEffect } from 'react'

export function useSSE(): void {
  useEffect(() => {
    const es = new EventSource('/api/events')

    const handleEvent = (e: MessageEvent): void => {
      window.dispatchEvent(new CustomEvent('task_updated', { detail: e.data }))
    }

    es.onmessage = handleEvent
    es.addEventListener('task_updated', handleEvent)

    return () => {
      es.close()
    }
  }, [])
}
