import { createBrowserRouter, RouterProvider, Navigate } from 'react-router-dom'
import { BoardPage } from './pages/BoardPage'
import { ProjectPage } from './pages/ProjectPage'

const router = createBrowserRouter([
  {
    path: '/',
    element: <BoardPage />,
  },
  {
    path: '/projects/:projectId',
    element: <ProjectPage />,
  },
  {
    path: '*',
    element: <Navigate to="/" replace />,
  },
])

export function App(): JSX.Element {
  return <RouterProvider router={router} />
}
