import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchProjects } from '../api/projects'
import { NewProjectModal } from '../components/NewProjectModal'

export function ProjectsPage() {
  const { data, isLoading, error } = useQuery({ queryKey: ['projects'], queryFn: fetchProjects })
  const [modalOpen, setModalOpen] = useState(false)

  return (
    <div className="page">
      <header className="page-header">
        <h1>Projects</h1>
        <button className="btn btn-primary" onClick={() => setModalOpen(true)}>
          New Project
        </button>
      </header>
      {isLoading && <div className="loading-state">Loading projects...</div>}
      {error && <div className="error-state">Failed to load projects</div>}
      {data && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Path</th>
              <th>Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {data.map(project => (
              <tr key={project.id}>
                <td>
                  <Link to={`/projects/${project.id}`}>{project.name}</Link>
                </td>
                <td>{project.local_path}</td>
                <td>{project.status}</td>
                <td>{new Date(project.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <NewProjectModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </div>
  )
}
