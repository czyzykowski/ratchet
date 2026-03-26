import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Markdown } from './Markdown'
import { createBootstrapSession, sendBootstrapMessage } from '../api/bootstrapChat'

interface BootstrapChatProps {
  onClose: () => void
  onBack: () => void
}

interface Message {
  role: 'user' | 'assistant'
  content: string
}

export function BootstrapChat({ onClose, onBack }: BootstrapChatProps) {
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  const [currentStream, setCurrentStream] = useState('')
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [chatError, setChatError] = useState<string | null>(null)
  const [sessionLoading, setSessionLoading] = useState(true)

  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const initialized = useRef(false)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentStream])

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true
      createBootstrapSession()
        .then(data => {
          setSessionId(data.session_id)
          setSessionLoading(false)
          setTimeout(() => inputRef.current?.focus(), 50)
        })
        .catch(err => {
          setChatError(err instanceof Error ? err.message : 'Failed to create session')
          setSessionLoading(false)
        })
    }
  }, [])

  async function sendMessage(userInput: string) {
    if (!sessionId) return

    setStreaming(true)
    setChatError(null)
    setCurrentStream('')
    setMessages(prev => [...prev, { role: 'user', content: userInput }])

    try {
      const res = await sendBootstrapMessage(sessionId, userInput)

      if (!res.ok) throw new Error(`Server error: ${res.status}`)
      if (!res.body) throw new Error('No response body')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let assistantText = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = JSON.parse(line.slice(6))
          if (payload.type === 'chunk') {
            assistantText += payload.text
            setCurrentStream(assistantText)
          } else if (payload.type === 'new_message') {
            setMessages(prev => [...prev, { role: 'assistant', content: assistantText }])
            assistantText = ''
            setCurrentStream('')
          } else if (payload.type === 'action_executed') {
            queryClient.invalidateQueries({ queryKey: ['projects'] })
            queryClient.invalidateQueries({ queryKey: ['board'] })
          }
        }
      }

      if (assistantText) {
        setMessages(prev => [...prev, { role: 'assistant', content: assistantText }])
        setCurrentStream('')
      }
    } catch (err) {
      setChatError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setStreaming(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  function handleSend() {
    const text = input.trim()
    if (!text || streaming || !sessionId) return
    setInput('')
    sendMessage(text)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="chat-container">
      <div className="chat-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <button className="btn btn-secondary btn-sm" onClick={onBack} style={{ padding: '0.25rem 0.5rem' }}>
            ← Back
          </button>
          <span className="chat-title">Bootstrap New Project</span>
        </div>
        <button className="modal-close" onClick={onClose}>&#215;</button>
      </div>

      <div className="chat-messages">
        {sessionLoading && (
          <div className="chat-thinking">Starting session...</div>
        )}
        {!sessionLoading && messages.length === 0 && !streaming && !chatError && (
          <div className="chat-thinking">Tell me about the project you want to build...</div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`chat-message chat-message-${msg.role}`}>
            <div className="chat-role">{msg.role === 'user' ? 'You' : 'Claude'}</div>
            <div className="chat-content">
              {msg.role === 'assistant' ? <Markdown content={msg.content} /> : msg.content}
            </div>
          </div>
        ))}

        {streaming && currentStream && (
          <div className="chat-message chat-message-assistant">
            <div className="chat-role">Claude</div>
            <div className="chat-content"><Markdown content={currentStream} isAnimating={true} /></div>
          </div>
        )}

        {streaming && !currentStream && (
          <div className="chat-thinking">Claude is thinking...</div>
        )}

        {chatError && (
          <div className="error-state" style={{ padding: '0.75rem' }}>{chatError}</div>
        )}

        <div ref={bottomRef} />
      </div>

      <div className="chat-input-area">
        <div className="chat-input-row">
          <textarea
            ref={inputRef}
            className="chat-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              sessionLoading ? 'Starting session...' :
              streaming ? 'Claude is responding...' :
              'Describe your project idea... (Enter to send, Shift+Enter for newline)'
            }
            disabled={streaming || !sessionId}
            rows={3}
          />
          <button
            className="btn btn-primary chat-send-btn"
            onClick={handleSend}
            disabled={streaming || !input.trim() || !sessionId}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
