import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'

interface Feature {
  id: string
  project_id: string
  title: string
  description: string
  project_name: string
  compiled_spec_count: number
  total_spec_count: number
}

interface FeaturesResponse {
  features: Feature[]
}

export function FeaturesPage() {
  const { data, isLoading, error } = useQuery<FeaturesResponse>({
    queryKey: ['features'],
    queryFn: () => apiFetch<FeaturesResponse>('/api/features'),
  })

  if (isLoading) return <div className="loading-state">Loading features...</div>
  if (error) return <div className="error-state">Failed to load features</div>
  if (!data) return null

  // Group by project
  const byProject = new Map<string, { project_name: string; features: Feature[] }>()
  for (const f of data.features) {
    if (!byProject.has(f.project_id)) {
      byProject.set(f.project_id, { project_name: f.project_name, features: [] })
    }
    byProject.get(f.project_id)!.features.push(f)
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>Features</h1>
      </header>

      {byProject.size === 0 && (
        <div className="loading-state">No features yet.</div>
      )}

      {Array.from(byProject.entries()).map(([projectId, group]) => (
        <section key={projectId} className="status-section">
          <h2 className="status-heading">{group.project_name}</h2>
          <table className="data-table">
            <tbody>
              {group.features.map(f => (
                <tr key={f.id} className="task-row">
                  <td>
                    <Link to={`/features/${f.id}`} style={{ color: '#e0e0e0', textDecoration: 'none' }}>
                      {f.title}
                    </Link>
                  </td>
                  <td style={{ color: '#a0a0a0', fontSize: '0.8rem' }}>
                    {f.description.slice(0, 80)}{f.description.length > 80 ? '…' : ''}
                  </td>
                  <td>
                    <span className="badge badge-in_progress">
                      {f.compiled_spec_count}/{f.total_spec_count} compiled
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  )
}
