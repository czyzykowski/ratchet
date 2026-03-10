import { NavLink, Route, Routes } from 'react-router-dom'
import { BoardPage } from './pages/BoardPage'
import { ProjectsPage } from './pages/ProjectsPage'
import { ProjectPage } from './pages/ProjectPage'

export function App() {
  return (
    <div>
      <nav className="spa-nav">
        <NavLink to="/" end>Board</NavLink>
        <NavLink to="/projects">Projects</NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<BoardPage />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/:project_id" element={<ProjectPage />} />
      </Routes>
    </div>
  )
}
