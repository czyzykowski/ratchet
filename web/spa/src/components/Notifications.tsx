import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useNotifications } from '../hooks/useNotifications'

export function Notifications() {
  const { notifications, dismissNotification } = useNotifications()
  const navigate = useNavigate()

  return (
    <div className="toast-container">
      {notifications.map(n => (
        <Toast
          key={n.id}
          id={n.id}
          taskId={n.taskId}
          taskTitle={n.taskTitle}
          message={n.message}
          onDismiss={dismissNotification}
          onNavigate={taskId => {
            navigate('/tasks/' + taskId)
            dismissNotification(n.id)
          }}
        />
      ))}
    </div>
  )
}

interface ToastProps {
  id: string
  taskId: string
  taskTitle: string
  message: string
  onDismiss(id: string): void
  onNavigate(taskId: string): void
}

function Toast({ id, taskId, taskTitle, message, onDismiss, onNavigate }: ToastProps) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(id), 5000)
    return () => clearTimeout(timer)
  }, [id, onDismiss])

  return (
    <div className="toast" onClick={() => onNavigate(taskId)}>
      <div className="toast-message">
        <div className="toast-title">{taskTitle}</div>
        <div>{message}</div>
      </div>
      <button
        className="toast-close"
        onClick={e => {
          e.stopPropagation()
          onDismiss(id)
        }}
        aria-label="Dismiss"
      >
        &#215;
      </button>
    </div>
  )
}
