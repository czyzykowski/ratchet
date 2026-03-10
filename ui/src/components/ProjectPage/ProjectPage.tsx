import type { Project, Task } from '../../api/types'
import { TaskCard } from '../TaskCard/TaskCard'

interface Props {
  project: Project
  tasks: Task[]
}

export function ProjectPage({ project, tasks }: Props): JSX.Element {
  return (
    <div style={{ padding: 16 }}>
      <h2>{project.name}</h2>
      <p style={{ color: '#555', fontSize: '0.9em' }}>{project.repo_url}</p>
      <div style={{ marginTop: 16 }}>
        {tasks.length === 0 ? (
          <p>No tasks.</p>
        ) : (
          tasks.map((task) => <TaskCard key={task.id} task={task} />)
        )}
      </div>
    </div>
  )
}
