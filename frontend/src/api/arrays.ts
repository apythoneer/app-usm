import { apiClient } from './client'
import type { ArraySummary, ArrayMetrics } from './types'

export const arraysApi = {
  list: (vendor?: string) =>
    apiClient.get<ArraySummary[]>('/arrays', { params: vendor ? { vendor } : {} }).then((r) => r.data),

  get: (arrayName: string) =>
    apiClient.get<ArrayMetrics>(`/arrays/${encodeURIComponent(arrayName)}`).then((r) => r.data),

  history: (arrayName: string, hours = 24) =>
    apiClient
      .get(`/analytics/history/${encodeURIComponent(arrayName)}`, { params: { hours } })
      .then((r) => r.data),
}
