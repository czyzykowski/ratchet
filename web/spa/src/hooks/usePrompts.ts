import { useQuery } from '@tanstack/react-query'
import { fetchPrompts } from '../api/settings'

export function usePrompts() {
  return useQuery({
    queryKey: ['settings-prompts'],
    queryFn: fetchPrompts,
    staleTime: 60_000,
  })
}
