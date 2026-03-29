import { useState } from 'react'
import { usePrompts } from '../hooks/usePrompts'
import type { PromptTemplate } from '../api/settings'

function PromptCard({ prompt }: { prompt: PromptTemplate }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="prompt-card">
      <div
        className="prompt-card-header"
        onClick={() => setExpanded(v => !v)}
        role="button"
        aria-expanded={expanded}
      >
        <span className="prompt-card-name">{prompt.name}</span>
        <span className="prompt-card-source">{prompt.source}</span>
        <span className="prompt-card-chevron">{expanded ? '▲' : '▼'}</span>
      </div>
      {expanded && (
        <div className="prompt-card-body">
          <p className="prompt-card-description">{prompt.description}</p>
          <div className="prompt-template">
            <pre className="prompt-template-code">{prompt.template}</pre>
          </div>
        </div>
      )}
    </div>
  )
}

function PromptsTab() {
  const { data, isLoading, error } = usePrompts()

  if (isLoading) return <div className="loading-state">Loading prompts...</div>
  if (error) return <div className="error-state">Failed to load prompts</div>
  if (!data) return null

  const grouped = data.data.reduce<Record<string, PromptTemplate[]>>((acc, p) => {
    if (!acc[p.category]) acc[p.category] = []
    acc[p.category].push(p)
    return acc
  }, {})

  return (
    <div>
      {Object.entries(grouped).map(([category, prompts]) => (
        <div key={category} className="prompt-category-group">
          <h2 className="prompt-category-heading">{category}</h2>
          {prompts.map(p => (
            <PromptCard key={p.name} prompt={p} />
          ))}
        </div>
      ))}
    </div>
  )
}

export function SettingsPage() {
  const [activeTab, setActiveTab] = useState<string>('prompts')

  return (
    <div className="page">
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      <div className="settings-tabs">
        <button
          className={`settings-tab${activeTab === 'prompts' ? ' active' : ''}`}
          onClick={() => setActiveTab('prompts')}
        >
          Prompts
        </button>
      </div>

      <div className="settings-tab-content">
        {activeTab === 'prompts' && <PromptsTab />}
      </div>
    </div>
  )
}
