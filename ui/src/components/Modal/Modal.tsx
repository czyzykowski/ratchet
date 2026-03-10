interface Props {
  onClose: () => void
  children: React.ReactNode
}

export function Modal({ onClose, children }: Props): JSX.Element {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{ background: '#fff', borderRadius: 8, padding: 24, minWidth: 320 }}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
        <button onClick={onClose} style={{ marginTop: 16 }}>
          Close
        </button>
      </div>
    </div>
  )
}
