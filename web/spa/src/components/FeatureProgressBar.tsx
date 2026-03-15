import { type ReactElement } from 'react'
import { STATUS_COLORS } from '../utils/statusColors'

interface FeatureProgressBarProps {
  compiledCount: number
  totalCount: number
  taskStatuses?: (string | null)[]
}

export function FeatureProgressBar({ compiledCount, totalCount, taskStatuses }: FeatureProgressBarProps) {
  if (totalCount === 0) {
    return <div style={{ color: '#6b7280', fontSize: '0.75rem' }}>No specs</div>
  }

  const segments: ReactElement[] = []
  for (let i = 0; i < totalCount; i++) {
    const isCompiled = i < compiledCount
    let color = '#374151' // pending: dark gray
    if (isCompiled) {
      const taskStatus = taskStatuses?.[i] ?? null
      color = taskStatus ? (STATUS_COLORS[taskStatus] ?? '#6b7280') : '#6b7280'
    }
    segments.push(
      <div
        key={i}
        style={{
          flex: 1,
          height: '8px',
          backgroundColor: color,
          borderRadius: i === 0 ? '4px 0 0 4px' : i === totalCount - 1 ? '0 4px 4px 0' : '0',
          marginRight: i < totalCount - 1 ? '1px' : '0',
          opacity: isCompiled ? 1 : 0.4,
        }}
      />
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', width: '100%', height: '8px' }}>
        {segments}
      </div>
      <div style={{ fontSize: '0.7rem', color: '#6b7280', marginTop: '0.2rem' }}>
        {compiledCount}/{totalCount} compiled
      </div>
    </div>
  )
}
