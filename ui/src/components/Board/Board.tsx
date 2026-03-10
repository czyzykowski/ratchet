import type { BoardResponse } from '../../api/types'
import { TaskCard } from '../TaskCard/TaskCard'

interface Props {
  data: BoardResponse
}

export function Board({ data }: Props): JSX.Element {
  return (
    <div style={{ display: 'flex', gap: 16, overflowX: 'auto', padding: 16 }}>
      {data.groups.map((group) => (
        <div
          key={group.status}
          style={{ minWidth: 200, flex: '0 0 220px' }}
        >
          <h3 style={{ fontSize: '0.85em', textTransform: 'uppercase', marginBottom: 8 }}>
            {group.label} ({group.tasks.length})
          </h3>
          {group.tasks.map((task) => (
            <TaskCard key={task.id} task={task} />
          ))}
        </div>
      ))}
    </div>
  )
}
