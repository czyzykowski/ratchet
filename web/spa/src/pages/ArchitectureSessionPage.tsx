import { useParams, useSearchParams } from 'react-router-dom'
import { ArchitectureChat } from '../components/ArchitectureChat'

export function ArchitectureSessionPage() {
  const { project_id } = useParams<{ project_id: string }>()
  const [searchParams] = useSearchParams()
  const scope = searchParams.get('scope') ?? undefined

  if (!project_id) {
    return <div className="error-state">Missing project ID</div>
  }

  return (
    <div className="page" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, padding: 0 }}>
      <ArchitectureChat projectId={project_id} scope={scope} />
    </div>
  )
}
