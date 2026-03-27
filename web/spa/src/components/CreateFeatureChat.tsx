import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Markdown } from './Markdown'

interface CreateFeatureChatProps {
  projectId: string
  onClose: () => void
  sessionId?: string
  initialMessage?: string
  featureId?: string
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  imageId?: string
}

interface FeaturePreview {
  title: string
  description: string
  spec_count: number
  raw: string
}

export function CreateFeatureChat({ projectId, onClose, sessionId: initialSessionId, initialMessage, featureId }: CreateFeatureChatProps) {
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
  const [pendingImageId, setPendingImageId] = useState<string | null>(null)
  const [pendingThumbnailUrl, setPendingThumbnailUrl] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentStream])

  useEffect(() => {
    const init = initialSessionId
      ? loadSession(initialSessionId)
      : createSession()
    init.then(({ sid, history }) => {
      if (history.length > 0) {
        const restored: Message[] = []
        for (const entry of history) {
          restored.push({ role: 'user', content: entry.content, imageId: entry.image_id ?? undefined })
          restored.push({ role: 'assistant', content: entry.assistant })
        }
        setMessages(restored)
      } else if (initialMessage && !initialSessionId) {
        sendMessage(initialMessage, sid)
        return
      }
      setTimeout(() => inputRef.current?.focus(), 50)
    })
  }, [])

  type SessionHistory = Array<{ content: string; assistant: string; image_id?: string | null }>
  type SessionResult = { sid: string; history: SessionHistory }

  async function createSession(): Promise<SessionResult> {
    const res = await fetch('/api/feature-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, force_new: true }),
    })
    const data = await res.json()
    const sid: string = data.session_id
    setSessionId(sid)
    const history: SessionHistory = (data.messages ?? []).map(
      (m: { content: string; assistant: string; image_id?: string | null }) => ({
        content: m.content,
        assistant: m.assistant,
        image_id: m.image_id,
      })
    )
    return { sid, history }
  }

  async function loadSession(sid: string): Promise<SessionResult> {
    const res = await fetch(`/api/feature-sessions/${sid}`)
    const data = await res.json()
    setSessionId(sid)
    const history: SessionHistory = (data.messages ?? []).map(
      (m: { content: string; assistant: string; image_id?: string | null }) => ({
        content: m.content,
        assistant: m.assistant,
        image_id: m.image_id,
      })
    )
    return { sid, history }
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

      const res = await fetch(`/api/feature-sessions/${activeSessionId}/message`, {
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
          } else if (payload.type === 'done' && payload.feature) {
            setDetectedFeature(payload.feature)
          }
        }
      }

      setMessages(prev => [...prev, { role: 'assistant', content: assistantText }])
      setCurrentStream('')
      if (sentThumbnailUrl) URL.revokeObjectURL(sentThumbnailUrl)
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

  async function saveFeature() {
    if (!detectedFeature) return
    setSaving(true)
    setSaveError(null)
    try {
      let res: Response
      if (featureId) {
        // Finalize existing idea feature with specs
        const finalizeBody: Record<string, unknown> = { feature_block: detectedFeature.raw }
        if (sessionId) finalizeBody.session_id = sessionId
        res = await fetch(`/api/features/${featureId}/finalize`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(finalizeBody),
        })
      } else {
        // Create new feature
        const saveBody: Record<string, unknown> = {
          project_id: projectId,
          feature_block: detectedFeature.raw,
        }
        if (sessionId) saveBody.session_id = sessionId
        res = await fetch('/api/features', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(saveBody),
        })
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? 'Failed to save feature')
      }
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['feature', featureId] })
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
        </div>
      )}
    </div>
  )
}
