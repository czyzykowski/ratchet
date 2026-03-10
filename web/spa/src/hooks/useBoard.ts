import { useQuery } from '@tanstack/react-query'

export interface BoardTask {
  id: string
  title: string
  project_name: string
  project_id: string
  status: string
  updated_at: string | null
  has_spec: boolean
  refinement_count: number
  unmet_deps: string[]
}

export interface BoardColumn {
  status: string
  label: string
  tasks: BoardTask[]
}

export interface BoardData {
  columns: BoardColumn[]
}

async function fetchBoard(): Promise<BoardData> {
  const res = await fetch('/api/board')
  if (!res.ok) throw new Error('Failed to fetch board')
  return res.json()
}

export function useBoard() {
  return useQuery({ queryKey: ['board'], queryFn: fetchBoard, staleTime: 30_000 })
}
