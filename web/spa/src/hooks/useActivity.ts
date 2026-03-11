import { useQuery } from '@tanstack/react-query'

export interface ActivityEvent {
  task_id: string
  task_title: string
  project_name: string
  from_status: string | null
  to_status: string
  occurred_at: string
}

async function fetchActivity(): Promise<{ events: ActivityEvent[] }> {
  const res = await fetch('/api/activity')
  if (!res.ok) throw new Error('Failed to fetch activity')
  return res.json()
}

export function useActivity() {
  return useQuery({ queryKey: ['activity'], queryFn: fetchActivity, staleTime: 10_000 })
}
