import { Board } from '../components/Board/Board'
import { useBoard } from '../hooks/useBoard'
import { useSSE } from '../hooks/useSSE'

export function BoardPage(): JSX.Element {
  useSSE()
  const { data, isLoading, error } = useBoard()

  if (isLoading) {
    return <div style={{ padding: 16 }}>Loading board...</div>
  }

  if (error || !data) {
    return <div style={{ padding: 16, color: 'red' }}>Failed to load board.</div>
  }

  return <Board data={data} />
}
