import { NavLink, Route, Routes } from 'react-router-dom'
import { BoardPage } from './pages/BoardPage'
import { FocusPage } from './pages/FocusPage'
import { ProjectsPage } from './pages/ProjectsPage'
import { ProjectPage } from './pages/ProjectPage'

export function App() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <nav className="spa-nav">
        <NavLink to="/" end>Focus</NavLink>
        <NavLink to="/board">Board</NavLink>
        <NavLink to="/projects">Projects</NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<FocusPage />} />
        <Route path="/board" element={<BoardPage />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/:project_id" element={<ProjectPage />} />
      </Routes>
    </div>
  )
}
