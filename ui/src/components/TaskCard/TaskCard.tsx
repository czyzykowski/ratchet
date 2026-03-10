import type { Task } from '../../api/types'

interface Props {
  task: Task
}

export function TaskCard({ task }: Props): JSX.Element {
  const unmetCount = task.unmet_dependencies?.length ?? 0

  return (
    <div style={{ border: '1px solid #ccc', borderRadius: 4, padding: '8px', marginBottom: 8 }}>
      <div style={{ fontWeight: 'bold', marginBottom: 4 }}>{task.title}</div>
      <div style={{ fontSize: '0.85em', color: '#555' }}>
        <span
          style={{
            background: '#eee',
            borderRadius: 3,
            padding: '2px 6px',
            marginRight: 6,
          }}
        >
          {task.status}
        </span>
        {(task.refinement_count ?? 0) > 0 && (
          <span style={{ marginRight: 6 }}>rev: {task.refinement_count}</span>
        )}
        {unmetCount > 0 && (
          <span style={{ color: '#c00' }}>unmet deps: {unmetCount}</span>
        )}
      </div>
    </div>
  )
}
