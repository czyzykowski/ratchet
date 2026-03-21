import { useState, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { BoardTask } from '../hooks/useBoard'
import { capabilityColor } from '../utils/capabilityColor'

interface TaskCardProps {
  task: BoardTask
  onOpen: (task: BoardTask) => void
}

function formatRelativeTime(isoString: string | null): string {
  if (!isoString) return 'unknown'
  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour}h ago`
  const diffDay = Math.floor(diffHour / 24)
  return `${diffDay}d ago`
}

export function TaskCard({ task, onOpen }: TaskCardProps) {
  const queryClient = useQueryClient()
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState(task.title)
  const inputRef = useRef<HTMLInputElement>(null)

  function startRename(e: React.MouseEvent) {
    e.stopPropagation()
    if (task.has_spec) return
    setRenameValue(task.title)
    setRenaming(true)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  async function commitRename() {
    if (renameValue.trim() === '' || renameValue === task.title) {
      setRenaming(false)
      return
    }
    try {
      await fetch(`/api/tasks/${task.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: renameValue }),
      })
      queryClient.invalidateQueries({ queryKey: ['board'] })
    } finally {
      setRenaming(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') commitRename()
    if (e.key === 'Escape') {
      setRenameValue(task.title)
      setRenaming(false)
    }
  }

  function handleCardClick() {
    if (renaming) return
    onOpen(task)
  }

  return (
    <div className="task-card" onClick={handleCardClick}>
      <div className="task-title">
        {renaming ? (
          <input
            ref={inputRef}
            className="rename-input"
            value={renameValue}
            onChange={e => setRenameValue(e.target.value)}
            onBlur={commitRename}
            onKeyDown={handleKeyDown}
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <span onDoubleClick={task.has_spec ? undefined : startRename}>
            {task.title}
          </span>
        )}
      </div>
      <div className="task-meta">
        <span className={`badge badge-${task.status}`}>{task.status.replace(/_/g, ' ')}</span>
        <span>{task.project_name}</span>
      </div>
      <div className="task-meta" style={{ marginTop: '0.25rem' }}>
        <span>{formatRelativeTime(task.updated_at)}</span>
        {task.refinement_count > 0 && <span>rev {task.refinement_count}</span>}
        {task.unmet_deps.length > 0 && (
          <span className="warning-text">&#9888; {task.unmet_deps.length} dep(s)</span>
        )}
      </div>
      {task.required_capabilities.length > 0 && (
        <div className="task-meta" style={{ marginTop: '0.25rem', flexWrap: 'wrap', gap: '0.25rem' }}>
          {task.required_capabilities.map(cap => {
            const colors = capabilityColor(cap)
            return (
              <span key={cap} className="badge" style={{ background: colors.background, color: colors.color }}>
                {cap}
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}
