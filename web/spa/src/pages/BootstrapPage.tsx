import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Markdown } from '../components/Markdown'
import {
  createBootstrapSession,
  listBootstrapSessions,
  loadBootstrapSession,
  sendBootstrapMessageWithImage,
  BootstrapSessionEntry,
} from '../api/bootstrapChat'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  imageId?: string
}

interface RegisteredProject {
  name: string
  projectId: string
}

function formatRelativeTime(isoString: string): string {
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

export function BootstrapPage() {
  const { session_id } = useParams<{ session_id?: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [currentStream, setCurrentStream] = useState('')
  const [input, setInput] = useState('')
  const [sessionLoading, setSessionLoading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [sessions, setSessions] = useState<BootstrapSessionEntry[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [pendingImageId, setPendingImageId] = useState<string | null>(null)
  const [pendingThumbnailUrl, setPendingThumbnailUrl] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [registeredProject, setRegisteredProject] = useState<RegisteredProject | null>(null)
  const [projectReady, setProjectReady] = useState(false)

  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const creatingSession = useRef(false)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentStream])

  async function fetchSessions() {
    setSessionsLoading(true)
    try {
      const data = await listBootstrapSessions()
      setSessions(data)
    } catch {
      // ignore
    } finally {
      setSessionsLoading(false)
    }
  }

  // Initialize or load session when session_id changes
  useEffect(() => {
    if (!session_id) {
      if (creatingSession.current) return
      creatingSession.current = true
      createBootstrapSession()
        .then(data => {
          navigate(`/bootstrap/${data.session_id}`, { replace: true })
        })
        .catch(err => {
          setChatError(err instanceof Error ? err.message : 'Failed to create session')
          creatingSession.current = false
        })
    } else {
      creatingSession.current = false
      setSessionLoading(true)
      setMessages([])
      setChatError(null)
      setRegisteredProject(null)
      setProjectReady(false)

      loadBootstrapSession(session_id)
        .then(data => {
          const restored: ChatMessage[] = []
          for (const entry of data.messages) {
            restored.push({
              role: 'user',
              content: entry.content,
              imageId: entry.image_id ?? undefined,
            })
            if (entry.assistant) {
              restored.push({ role: 'assistant', content: entry.assistant })
            }
          }
          setMessages(restored)
          setSessionLoading(false)
          setTimeout(() => inputRef.current?.focus(), 50)
        })
        .catch(err => {
          setChatError(err instanceof Error ? err.message : 'Failed to load session')
          setSessionLoading(false)
        })
    }
  }, [session_id, navigate])

  // Load sessions list on mount
  useEffect(() => {
    fetchSessions()
  }, [])

  async function handleNewSession() {
    try {
      const data = await createBootstrapSession()
      await fetchSessions()
      navigate(`/bootstrap/${data.session_id}`)
    } catch (err) {
      setChatError(err instanceof Error ? err.message : 'Failed to create session')
    }
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

  async function sendMessage(userInput: string) {
    if (!session_id) return

    const sentImageId = pendingImageId

    setStreaming(true)
    setChatError(null)
    setCurrentStream('')
    setMessages(prev => [
      ...prev,
      { role: 'user', content: userInput, imageId: sentImageId ?? undefined },
    ])
    clearPendingImage()

    try {
      const res = await sendBootstrapMessageWithImage(
        session_id,
        userInput,
        sentImageId ?? undefined,
      )

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

            const { action, result } = payload as {
              action: string
              result: {
                success: boolean
                message: string
                entity_id?: string
                error?: string
              }
            }

            if (action === 'register_project' && result.success && result.entity_id) {
              const nameMatch = result.message.match(/['"]([^'"]+)['"]/)
              const projectName = nameMatch ? nameMatch[1] : result.message || 'Project'
              setRegisteredProject({ name: projectName, projectId: result.entity_id })
            } else if (action === 'check_task_status' && result.success) {
              const msg = (result.message ?? '').toLowerCase()
              if (
                msg.includes('complet') ||
                msg.includes('done') ||
                msg.includes('finished') ||
                msg.includes('merged')
              ) {
                setProjectReady(true)
              }
            }
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
      await fetchSessions()
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  function handleSend() {
    const text = input.trim()
    if (!text || streaming || !session_id) return
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
    <div className="bootstrap-page">
      <div className="chat-with-sidebar">
        {/* Sidebar */}
        <div className="chat-session-list">
          <button
            className="chat-session-new-btn"
            onClick={handleNewSession}
            disabled={streaming}
          >
            + New Session
          </button>
          {sessionsLoading && (
            <div style={{ padding: '0.5rem', fontSize: '0.8rem', color: '#6b7280' }}>
              Loading...
            </div>
          )}
          {sessions.map(s => (
            <NavLink
              key={s.session_id}
              to={`/bootstrap/${s.session_id}`}
              className={({ isActive }) => `chat-session-item${isActive ? ' active' : ''}`}
            >
              <span className="bootstrap-session-label">
                {formatRelativeTime(s.created_at)}
              </span>
            </NavLink>
          ))}
          {!sessionsLoading && sessions.length === 0 && (
            <div style={{ padding: '0.5rem', fontSize: '0.8rem', color: '#9ca3af' }}>
              No sessions yet
            </div>
          )}
        </div>

        {/* Main chat area */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <div className="chat-header">
            <span className="chat-title">Bootstrap New Project</span>
          </div>

          <div className="chat-messages">
            {!session_id && !chatError && (
              <div className="chat-thinking">Creating session...</div>
            )}
            {sessionLoading && (
              <div className="chat-thinking">Loading session...</div>
            )}
            {!sessionLoading &&
              messages.length === 0 &&
              !streaming &&
              !chatError &&
              session_id && (
                <div className="chat-thinking">
                  Tell me about the project you want to build...
                </div>
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
                  {msg.role === 'assistant' ? (
                    <Markdown content={msg.content} />
                  ) : (
                    msg.content
                  )}
                </div>
              </div>
            ))}

            {streaming && currentStream && (
              <div className="chat-message chat-message-assistant">
                <div className="chat-role">Claude</div>
                <div className="chat-content">
                  <Markdown content={currentStream} isAnimating={true} />
                </div>
              </div>
            )}

            {streaming && !currentStream && (
              <div className="chat-thinking">Claude is thinking...</div>
            )}

            {chatError && (
              <div className="error-state" style={{ padding: '0.75rem' }}>
                {chatError}
              </div>
            )}

            {registeredProject && (
              <div className="bootstrap-banner">
                Project &lsquo;{registeredProject.name}&rsquo; registered!{' '}
                <Link to={`/projects/${registeredProject.projectId}`}>View Project</Link>
              </div>
            )}

            {projectReady && registeredProject && (
              <div className="bootstrap-celebration">
                Project Ready!{' '}
                <Link to={`/projects/${registeredProject.projectId}`}>
                  Go to Project
                </Link>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          <div className="chat-input-area">
            {(pendingThumbnailUrl || uploadError) && (
              <div className="chat-image-preview-row">
                {pendingThumbnailUrl && (
                  <div className="chat-image-preview">
                    <img src={pendingThumbnailUrl} alt="Image pending upload" />
                    <button
                      className="chat-image-remove"
                      onClick={clearPendingImage}
                      title="Remove image"
                    >
                      &#215;
                    </button>
                  </div>
                )}
                {uploadError && (
                  <span className="chat-upload-error">{uploadError}</span>
                )}
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
                disabled={streaming || !session_id}
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
                  sessionLoading
                    ? 'Loading session...'
                    : !session_id
                      ? 'Creating session...'
                      : streaming
                        ? 'Claude is responding...'
                        : 'Describe your project idea... (Enter to send, Shift+Enter for newline)'
                }
                disabled={streaming || !session_id || sessionLoading}
                rows={3}
              />
              <button
                className="btn btn-primary chat-send-btn"
                onClick={handleSend}
                disabled={streaming || !input.trim() || !session_id || sessionLoading}
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
