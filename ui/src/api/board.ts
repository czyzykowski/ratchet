import type { BoardResponse } from './types'

export function fetchBoard(): Promise<BoardResponse> {
  return fetch('/api/board').then((r) => r.json())
}
