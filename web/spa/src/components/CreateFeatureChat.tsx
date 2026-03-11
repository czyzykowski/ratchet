import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

interface CreateFeatureChatProps {
  projectId: string
  onClose: () => void
}

interface Message {
  role: 'user' | 'assistant'
  content: string
}

interface FeaturePreview {
  title: string
  description: string
  spec_count: number
  raw: string
}

export function CreateFeatureChat({ projectId, onClose }: CreateFeatureChatProps) {
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  const [currentStream, setCurrentStream] = useState('')
  const [input, setInput] = useState('')
  const [detectedFeature, setDetectedFeature] = useState<FeaturePreview | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [chatError, setChatError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentStream])

  useEffect(() => {
    createSession()
    return () => {
      if (sessionIdRef.current) {
        fetch(`/api/feature-sessions/${sessionIdRef.current}`, { method: 'DELETE' })
      }
    }
  }, [])

  async function createSession() {
    const res = await fetch('/api/feature-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId }),
    })
    const data = await res.json()
    const sid: string = data.session_id
    setSessionId(sid)
    sessionIdRef.current = sid
    setTimeout(() => inputRef.current?.focus(), 50)
  }

  async function sendMessage(userInput: string) {
    if (!sessionId) return
    setStreaming(true)
    setChatError(null)
    setCurrentStream('')
    setMessages(prev => [...prev, { role: 'user', content: userInput }])

    try {
      const res = await fetch(`/api/feature-sessions/${sessionId}/message`, {
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
          } else if (payload.type === 'done' && payload.feature) {
            setDetectedFeature(payload.feature)
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

  async function saveFeature() {
    if (!detectedFeature) return
    setSaving(true)
    setSaveError(null)
    try {
      const res = await fetch('/api/features', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId, feature_block: detectedFeature.raw }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? 'Failed to save feature')
      }
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
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
        <span className="chat-title">Add Feature</span>
        <button className="modal-close" onClick={onClose}>&#215;</button>
      </div>

      <div className="chat-messages">
        {messages.length === 0 && !streaming && !chatError && (
          <div className="chat-thinking">Describe the feature you want to build...</div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`chat-message chat-message-${msg.role}`}>
            <div className="chat-role">{msg.role === 'user' ? 'You' : 'Claude'}</div>
            <div className="chat-content">{msg.content}</div>
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

      {detectedFeature && (
        <div className="chat-spec-ready">
          <div className="chat-spec-label">
            Feature Ready — {detectedFeature.spec_count} spec{detectedFeature.spec_count !== 1 ? 's' : ''}
            {saveError && <span className="chat-spec-error">{saveError}</span>}
          </div>
          <div style={{ fontSize: '0.8rem', color: '#6b7280', marginBottom: '0.25rem' }}>
            <strong style={{ color: '#1a1a1a' }}>{detectedFeature.title}</strong>
            {detectedFeature.description && (
              <p style={{ marginTop: '0.25rem' }}>{detectedFeature.description.slice(0, 200)}{detectedFeature.description.length > 200 ? '…' : ''}</p>
            )}
          </div>
          <pre className="chat-spec-preview">{detectedFeature.raw}</pre>
          <div className="chat-spec-actions">
            <button className="btn btn-secondary" onClick={() => setDetectedFeature(null)}>
              Continue Editing
            </button>
            <button className="btn btn-primary" onClick={saveFeature} disabled={saving}>
              {saving ? 'Saving...' : 'Save Feature'}
            </button>
          </div>
        </div>
      )}

      {!detectedFeature && (
        <div className="chat-input-row">
          <textarea
            ref={inputRef}
            className="chat-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              !sessionId ? 'Connecting...' :
              streaming ? 'Claude is responding...' :
              messages.length === 0 ? 'Describe the feature you want to build… (Enter to send)' :
              'Reply… (Enter to send, Shift+Enter for newline, type "done" to generate)'
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
      )}
    </div>
  )
}
