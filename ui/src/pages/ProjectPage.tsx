import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchProject } from '../api/projects'
import { ProjectPage as ProjectPageComponent } from '../components/ProjectPage/ProjectPage'

export function ProjectPage(): JSX.Element {
  const { projectId } = useParams<{ projectId: string }>()

  const { data, isLoading, error } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => fetchProject(projectId ?? ''),
    enabled: !!projectId,
  })

  if (isLoading) {
    return <div style={{ padding: 16 }}>Loading project...</div>
  }

  if (error || !data) {
    return <div style={{ padding: 16, color: 'red' }}>Failed to load project.</div>
  }

  return <ProjectPageComponent project={data.project} tasks={data.tasks} />
}
