import { NavLink, Route, Routes } from 'react-router-dom'
import { BoardPage } from './pages/BoardPage'
import { ExecutionDetailPage } from './pages/ExecutionDetailPage'
import { FeatureDetailPage } from './pages/FeatureDetailPage'
import { FeaturesPage } from './pages/FeaturesPage'
import { FocusPage } from './pages/FocusPage'
import { ProjectsPage } from './pages/ProjectsPage'
import { ProjectPage } from './pages/ProjectPage'
import { SpecDetailPage } from './pages/SpecDetailPage'
import { TaskDetailPage } from './pages/TaskDetailPage'

export function App() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <nav className="spa-nav">
        <NavLink to="/" end>Focus</NavLink>
        <NavLink to="/board">Board</NavLink>
        <NavLink to="/projects">Projects</NavLink>
        <NavLink to="/features">Features</NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<FocusPage />} />
        <Route path="/board" element={<BoardPage />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/:project_id" element={<ProjectPage />} />
        <Route path="/tasks/:task_id" element={<TaskDetailPage />} />
        <Route path="/specs/:spec_id" element={<SpecDetailPage />} />
        <Route path="/features" element={<FeaturesPage />} />
        <Route path="/features/:feature_id" element={<FeatureDetailPage />} />
        <Route path="/executions/:execution_id" element={<ExecutionDetailPage />} />
      </Routes>
    </div>
  )
}
