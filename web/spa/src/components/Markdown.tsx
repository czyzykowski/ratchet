import { useState, useEffect } from 'react'
import { Streamdown } from 'streamdown'
import 'streamdown/styles.css'
import './Markdown.css'

let mermaidPlugin: Record<string, unknown> | null = null
let mermaidLoading = false
const mermaidListeners: Array<() => void> = []

function loadMermaid() {
  if (mermaidPlugin) return Promise.resolve()
  if (mermaidLoading) return new Promise<void>(r => mermaidListeners.push(r))
  mermaidLoading = true
  return import('@streamdown/mermaid').then(mod => {
    mermaidPlugin = { mermaid: mod.mermaid }
    mermaidListeners.forEach(fn => fn())
    mermaidListeners.length = 0
  })
}

interface MarkdownProps {
  content: string
  isAnimating?: boolean
}

export function Markdown({ content, isAnimating }: MarkdownProps) {
  const hasMermaid = content.includes('```mermaid')
  const [plugins, setPlugins] = useState<Record<string, unknown>>(mermaidPlugin && hasMermaid ? mermaidPlugin : {})

  useEffect(() => {
    if (hasMermaid && !mermaidPlugin) {
      loadMermaid().then(() => { if (mermaidPlugin) setPlugins(mermaidPlugin) })
    }
  }, [hasMermaid])

  return (
    <div className="md-prose">
      <Streamdown isAnimating={isAnimating ?? false} plugins={plugins}>{content}</Streamdown>
    </div>
  )
}
