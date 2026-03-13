import { Streamdown } from 'streamdown'
import 'streamdown/styles.css'
import './Markdown.css'

interface MarkdownProps {
  content: string
  isAnimating?: boolean
}

export function Markdown({ content, isAnimating }: MarkdownProps) {
  return (
    <div className="md-prose">
      <Streamdown isAnimating={isAnimating ?? false}>{content}</Streamdown>
    </div>
  )
}
