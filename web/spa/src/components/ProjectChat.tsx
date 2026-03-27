import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Markdown } from './Markdown'
import { useSSE } from '../hooks/useSSE'

interface ProjectChatProps {
  projectId: string
}

interface SessionEntry {
  session_id: string
  created_at: string
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  imageId?: string
}

function formatRelativeTime(isoString: string | null | undefined): string {
  if (!isoString) return 'unknown'
  const date = new Date(isoString)
  const diffMs = Date.now() - date.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour}h ago`
  return `${Math.floor(diffHour / 24)}d ago`
}

export function ProjectChat({ projectId }: ProjectChatProps) {
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  const [currentStream, setCurrentStream] = useState('')
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [pendingImageId, setPendingImageId] = useState<string | null>(null)
  const [pendingThumbnailUrl, setPendingThumbnailUrl] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [chatError, setChatError] = useState<string | null>(null)
  const [sessions, setSessions] = useState<SessionEntry[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(false)

  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useSSE((event) => {
    if (event.type === 'action_executed') {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['board'] })
      queryClient.invalidateQueries({ queryKey: ['project-features', projectId] })
    }
  })

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentStream])

  useEffect(() => {
    initSession()
  }, [])

  async function initSession() {
    await createSession()
    await fetchSessions()
  }

  async function fetchSessions() {
    setSessionsLoading(true)
    try {
      const res = await fetch(`/api/project-chat-sessions?project_id=${projectId}`)
      if (res.ok) {
        const data = await res.json()
        setSessions(data)
      }
    } finally {
      setSessionsLoading(false)
    }
  }

  async function createSession() {
    const res = await fetch('/api/project-chat-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId }),
    })
    const data = await res.json()
    const sid: string = data.session_id
    setSessionId(sid)
    setMessages([])
    setChatError(null)
    await fetchSessions()
    setTimeout(() => inputRef.current?.focus(), 50)
  }

  async function loadSession(sid: string) {
    const res = await fetch(`/api/project-chat-sessions/${sid}`)
    const data = await res.json()
    setSessionId(sid)
    setChatError(null)
    const restored: Message[] = []
    for (const entry of (data.messages ?? [])) {
      restored.push({ role: 'user', content: entry.content, imageId: entry.image_id ?? undefined })
      if (entry.assistant) {
        restored.push({ role: 'assistant', content: entry.assistant })
      }
    }
    setMessages(restored)
    setTimeout(() => inputRef.current?.focus(), 50)
  }

  async function uploadImage(file: File): Promise<string | null> {
    setUploadError(null)
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await fetch('/api/chat-images', { method: 'POST', body: form })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setUploadError(body.detail ?? `Upload failed: ${res.status}`)
        return null
      }
      const data = await res.json()
      return data.image_id as string
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed')
      return null
    }
  }

  async function handleFileSelect(file: File) {
    const url = URL.createObjectURL(file)
    setPendingThumbnailUrl(url)
    const id = await uploadImage(file)
    if (id) {
      setPendingImageId(id)
    } else {
      setPendingThumbnailUrl(null)
    }
  }

  function handleAttachClick() {
    fileInputRef.current?.click()
  }

  function handleFileInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) handleFileSelect(file)
    e.target.value = ''
  }

  function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const items = e.clipboardData?.items
    if (!items) return
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) {
          e.preventDefault()
          handleFileSelect(file)
        }
        break
      }
    }
  }

  function clearPendingImage() {
    if (pendingThumbnailUrl) URL.revokeObjectURL(pendingThumbnailUrl)
    setPendingImageId(null)
    setPendingThumbnailUrl(null)
    setUploadError(null)
  }

  async function sendMessage(userInput: string, sid?: string) {
    const activeSessionId = sid ?? sessionId
    if (!activeSessionId) return

    const sentImageId = pendingImageId
    const sentThumbnailUrl = pendingThumbnailUrl

    setStreaming(true)
    setChatError(null)
    setCurrentStream('')
    setMessages(prev => [...prev, { role: 'user', content: userInput, imageId: sentImageId ?? undefined }])
    clearPendingImage()

    try {
      const body: Record<string, unknown> = { user_input: userInput }
      if (sentImageId) body.image_id = sentImageId

      const res = await fetch(`/api/project-chat-sessions/${activeSessionId}/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
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
          } else if (payload.type === 'new_message') {
            setMessages(prev => [...prev, { role: 'assistant', content: assistantText }])
            assistantText = ''
            setCurrentStream('')
          } else if (payload.type === 'action_executed') {
            queryClient.invalidateQueries({ queryKey: ['board'] })
            queryClient.invalidateQueries({ queryKey: ['features'] })
          }
        }
      }

      setMessages(prev => [...prev, { role: 'assistant', content: assistantText }])
      setCurrentStream('')
      if (sentThumbnailUrl) URL.revokeObjectURL(sentThumbnailUrl)

      // Refresh session list after first message creates a new session entry
      await fetchSessions()
    } catch (err) {
      setChatError(err instanceof Error ? err.message : 'Unknown error')
      if (sentThumbnailUrl) URL.revokeObjectURL(sentThumbnailUrl)
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
    <div className="chat-container" style={{ height: '100%' }}>
      <div className="chat-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <Link to={`/projects/${projectId}`} className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '0.25rem 0.75rem' }}>
            ← Back
          </Link>
          <span className="chat-title">Project Chat</span>
        </div>
      </div>

      <div className="chat-with-sidebar">
        <div className="chat-session-list">
          <button
            className="chat-session-new-btn"
            onClick={createSession}
            disabled={streaming}
          >
            + New Chat
          </button>
          {sessionsLoading && <div style={{ padding: '0.5rem', fontSize: '0.8rem', color: '#6b7280' }}>Loading...</div>}
          {sessions.map(s => (
            <div
              key={s.session_id}
              className={`chat-session-item${s.session_id === sessionId ? ' active' : ''}`}
              onClick={() => loadSession(s.session_id)}
            >
              {formatRelativeTime(s.created_at)}
            </div>
          ))}
          {!sessionsLoading && sessions.length === 0 && (
            <div style={{ padding: '0.5rem', fontSize: '0.8rem', color: '#9ca3af' }}>No sessions yet</div>
          )}
        </div>

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <div className="chat-messages">
            {messages.length === 0 && !streaming && !chatError && sessionId && (
              <div className="chat-thinking">Start a conversation about this project...</div>
            )}
            {messages.length === 0 && !streaming && !chatError && !sessionId && (
              <div className="chat-thinking">Select a session or create a new one to start chatting...</div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={`chat-message chat-message-${msg.role}`}>
                <div className="chat-role">{msg.role === 'user' ? 'You' : 'Claude'}</div>
                <div className="chat-content">
                  {msg.imageId && (
                    <img
                      src={`/api/chat-images/${msg.imageId}`}
                      alt="User uploaded image"
                      className="chat-inline-image"
                  loading="lazy"
                    />
                  )}
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
            {(pendingThumbnailUrl || uploadError) && (
              <div className="chat-image-preview-row">
                {pendingThumbnailUrl && (
                  <div className="chat-image-preview">
                    <img src={pendingThumbnailUrl} alt="Image pending upload" />
                    <button className="chat-image-remove" onClick={clearPendingImage} title="Remove image">&#215;</button>
                  </div>
                )}
                {uploadError && <span className="chat-upload-error">{uploadError}</span>}
              </div>
            )}
            <div className="chat-input-row">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                style={{ display: 'none' }}
                onChange={handleFileInputChange}
              />
              <button
                className="btn btn-secondary chat-attachment-btn"
                onClick={handleAttachClick}
                disabled={streaming || !sessionId}
                title="Attach image"
              >
                &#128206;
              </button>
              <textarea
                ref={inputRef}
                className="chat-input"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                placeholder={
                  !sessionId ? 'Select or create a session to start...' :
                  streaming ? 'Claude is responding...' :
                  'Message Claude... (Enter to send, Shift+Enter for newline)'
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
      </div>
    </div>
  )
}
