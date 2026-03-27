import { useParams } from 'react-router-dom'
import { ArchitectureChat } from '../components/ArchitectureChat'

export function ArchitectureSessionPage() {
  const { project_id } = useParams<{ project_id: string }>()

  if (!project_id) {
    return <div className="error-state">Missing project ID</div>
  }

  return (
    <div className="page" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, padding: 0 }}>
      <ArchitectureChat projectId={project_id} />
    </div>
  )
}
