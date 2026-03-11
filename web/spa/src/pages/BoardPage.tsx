import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useBoard, type BoardTask } from '../hooks/useBoard'
import { useSSE } from '../hooks/useSSE'
import { TaskCard } from '../components/TaskCard'
import { TaskDetailModal } from '../components/TaskDetailModal'

export function BoardPage() {
  const { data, isLoading, error } = useBoard()
  const queryClient = useQueryClient()
  useSSE(() => {
    queryClient.invalidateQueries({ queryKey: ['board'] })
  })
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)

  return (
    <div className="board-page">
      <header className="board-header">
        <div className="board-title">RATCHET BOARD</div>
      </header>
      {isLoading && <div className="loading-state">Loading board...</div>}
      {error && <div className="error-state">Failed to load board</div>}
      {data && (
        <div className="board-columns">
          {data.columns.map(column => (
            <section key={column.status} className="board-column">
              <h2 className="column-header">
                {column.label} ({column.tasks.length})
              </h2>
              {column.tasks.map(task => (
                <TaskCard
                  key={task.id}
                  task={task}
                  onOpen={(t: BoardTask) => setSelectedTaskId(t.id)}
                />
              ))}
            </section>
          ))}
        </div>
      )}
      <TaskDetailModal
        taskId={selectedTaskId}
        onClose={() => setSelectedTaskId(null)}
      />
    </div>
  )
}
