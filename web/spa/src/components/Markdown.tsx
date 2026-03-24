import { Streamdown } from 'streamdown'
import 'streamdown/styles.css'
import './Markdown.css'
import { mermaid } from '@streamdown/mermaid'

interface MarkdownProps {
  content: string
  isAnimating?: boolean
}

export function Markdown({ content, isAnimating }: MarkdownProps) {
  return (
    <div className="md-prose">
      <Streamdown isAnimating={isAnimating ?? false} plugins={{ mermaid }}>{content}</Streamdown>
    </div>
  )
}
