import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Markdown } from './Markdown'

interface CreateSpecChatProps {
  taskId: string
  taskTitle: string
  onClose: () => void
}

interface Message {
  role: 'user' | 'assistant'
  content: string
}

export function CreateSpecChat({ taskId, taskTitle, onClose }: CreateSpecChatProps) {
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  const [currentStream, setCurrentStream] = useState('')
  const [input, setInput] = useState('')
  const [detectedSpec, setDetectedSpec] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [chatError, setChatError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const initialized = useRef(false)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentStream])

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true
      createSession().then(({ sid, history }) => {
        if (history.length > 0) {
          const restored: Message[] = []
          for (const entry of history) {
            restored.push({ role: 'user', content: entry.content })
            restored.push({ role: 'assistant', content: entry.assistant })
          }
          setMessages(restored)
        } else {
          sendMessage(taskTitle, sid)
        }
      })
    }
  }, [])

  async function createSession(): Promise<{ sid: string; history: Array<{ content: string; assistant: string }> }> {
    const res = await fetch('/api/spec-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId }),
    })
    const data = await res.json()
    const sid: string = data.session_id
    setSessionId(sid)
    const history: Array<{ content: string; assistant: string }> = (data.messages ?? []).map(
      (m: { content: string; assistant: string }) => ({ content: m.content, assistant: m.assistant })
    )
    return { sid, history }
  }

  async function sendMessage(userInput: string, sid?: string) {
    const activeSessionId = sid ?? sessionId
    if (!activeSessionId) return

    setStreaming(true)
    setChatError(null)
    setCurrentStream('')
    setMessages(prev => [...prev, { role: 'user', content: userInput }])

    try {
      const res = await fetch(`/api/spec-sessions/${activeSessionId}/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_input: userInput }),
      })

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
          } else if (payload.type === 'done') {
            if (payload.spec) {
              setDetectedSpec(payload.spec)
            }
          }
        }
      }

      setMessages(prev => [...prev, { role: 'assistant', content: assistantText }])
      setCurrentStream('')
    } catch (err) {
      setChatError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setStreaming(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  function handleSend() {
    const text = input.trim()
    if (!text || streaming) return
    setInput('')
    sendMessage(text)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  async function saveSpec() {
    if (!detectedSpec) return
    setSaving(true)
    setSaveError(null)
    try {
      const res = await fetch(`/api/tasks/${taskId}/spec`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: detectedSpec }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? 'Failed to save spec')
      }
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
      queryClient.invalidateQueries({ queryKey: ['board'] })
      onClose()
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="chat-container">
      <div className="chat-header">
        <span className="chat-title">Create Spec — {taskTitle}</span>
        <button className="modal-close" onClick={onClose}>&#215;</button>
      </div>

      <div className="chat-messages">
        {messages.length === 0 && !streaming && !chatError && (
          <div className="chat-thinking">Claude is thinking...</div>
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
            <div className="chat-content">{currentStream}</div>
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

      {detectedSpec && (
        <div className="chat-spec-ready">
          <div className="chat-spec-label">
            Spec Ready
            {saveError && <span className="chat-spec-error">{saveError}</span>}
          </div>
          <pre className="chat-spec-preview">{detectedSpec}</pre>
          <div className="chat-spec-actions">
            <button className="btn btn-secondary" onClick={() => setDetectedSpec(null)}>
              Continue Editing
            </button>
            <button className="btn btn-primary" onClick={saveSpec} disabled={saving}>
              {saving ? 'Saving...' : 'Save & Assign Spec'}
            </button>
          </div>
        </div>
      )}

      {!detectedSpec && (
        <div className="chat-input-row">
          <textarea
            ref={inputRef}
            className="chat-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={streaming ? 'Claude is responding...' : 'Type a message… (Enter to send, Shift+Enter for newline)'}
            disabled={streaming}
            rows={3}
          />
          <button
            className="btn btn-primary chat-send-btn"
            onClick={handleSend}
            disabled={streaming || !input.trim()}
          >
            Send
          </button>
        </div>
      )}
    </div>
  )
}
