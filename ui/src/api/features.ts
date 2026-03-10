import type { FeaturesResponse } from './types'

export function fetchFeatures(): Promise<FeaturesResponse> {
  return fetch('/api/features').then((r) => r.json())
}
