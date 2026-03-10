export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    const err = new Error(body.detail ?? res.statusText) as Error & { status: number; detail: string }
    err.status = res.status
    err.detail = body.detail ?? res.statusText
    throw err
  }
  return res.json() as Promise<T>
}
