import { useEffect, useRef, useState } from 'react'
import type { LogEntry } from '../api/worker'

export function useWorkerLogs(maxEntries = 500): { logs: LogEntry[]; connected: boolean } {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [connected, setConnected] = useState(false)
  const retryDelay = useRef(1000)

  useEffect(() => {
    let es: EventSource
    let timeoutId: ReturnType<typeof setTimeout>

    function connect() {
      es = new EventSource('/api/worker/logs')

      es.onopen = () => {
        retryDelay.current = 1000
        setConnected(true)
      }

      es.addEventListener('worker_log_batch', (e: MessageEvent) => {
        try {
          const batch = JSON.parse(e.data as string) as LogEntry[]
          setLogs(batch.slice(-maxEntries))
        } catch {
          // ignore parse errors
        }
      })

      es.addEventListener('worker_log', (e: MessageEvent) => {
        try {
          const entry = JSON.parse(e.data as string) as LogEntry
          setLogs(prev => {
            const next = [...prev, entry]
            return next.length > maxEntries ? next.slice(next.length - maxEntries) : next
          })
        } catch {
          // ignore parse errors
        }
      })

      es.onerror = () => {
        setConnected(false)
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
  }, [maxEntries])

  return { logs, connected }
}
