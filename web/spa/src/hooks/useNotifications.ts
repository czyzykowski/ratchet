import { createContext, useContext, useState } from 'react'

export interface Notification {
  id: string
  message: string
  taskId: string
  taskTitle: string
}

interface NotificationsContextValue {
  notifications: Notification[]
  addNotification(n: Omit<Notification, 'id'>): void
  dismissNotification(id: string): void
}

export const NotificationsContext = createContext<NotificationsContextValue | null>(null)

export function useNotificationsState(): NotificationsContextValue {
  const [notifications, setNotifications] = useState<Notification[]>([])

  function addNotification(n: Omit<Notification, 'id'>) {
    const id = crypto.randomUUID()
    setNotifications(prev => [...prev, { ...n, id }])
  }

  function dismissNotification(id: string) {
    setNotifications(prev => prev.filter(n => n.id !== id))
  }

  return { notifications, addNotification, dismissNotification }
}

export function useNotifications(): NotificationsContextValue {
  const ctx = useContext(NotificationsContext)
  if (!ctx) throw new Error('useNotifications must be used inside NotificationsProvider')
  return ctx
}
