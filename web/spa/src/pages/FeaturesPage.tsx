import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { FeatureProgressBar } from '../components/FeatureProgressBar'

interface Feature {
  id: string
  project_id: string
  title: string
  description: string
  project_name: string
  compiled_spec_count: number
  total_spec_count: number
  status: string
}

interface FeaturesResponse {
  features: Feature[]
}

export function FeaturesPage() {
  const { data, isLoading, error } = useQuery<FeaturesResponse>({
    queryKey: ['features'],
    queryFn: () => apiFetch<FeaturesResponse>('/api/features?include_abandoned=false'),
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
                    <Link to={`/features/${f.id}`}>
                      {f.title}
                    </Link>
                  </td>
                  <td className="text-secondary text-xs" style={{ maxWidth: '300px' }}>
                    {f.description.slice(0, 80)}{f.description.length > 80 ? '...' : ''}
                  </td>
                  <td>
                    <span className={`badge badge-${f.status}`}>{f.status.replace(/_/g, ' ')}</span>
                  </td>
                  <td style={{ minWidth: '120px' }}>
                    <FeatureProgressBar
                      compiledCount={f.compiled_spec_count}
                      totalCount={f.total_spec_count}
                    />
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
