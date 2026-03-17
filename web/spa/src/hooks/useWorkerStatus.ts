import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchWorkerStatus,
  restartWorker,
  startWorker,
  stopWorker,
  updateWorkerSettings,
} from '../api/worker'
import type { WorkerSettings } from '../api/worker'

export function useWorkerStatus() {
  return useQuery({
    queryKey: ['worker-status'],
    queryFn: fetchWorkerStatus,
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}

export function useStartWorker() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: startWorker,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['worker-status'] })
    },
  })
}

export function useStopWorker() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (graceful?: boolean) => stopWorker(graceful),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['worker-status'] })
    },
  })
}

export function useRestartWorker() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (graceful?: boolean) => restartWorker(graceful),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['worker-status'] })
    },
  })
}

export function useUpdateWorkerSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (settings: Partial<WorkerSettings>) => updateWorkerSettings(settings),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['worker-status'] })
    },
  })
}
