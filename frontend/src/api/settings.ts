import { apiClient } from './client'
import type { DBTableInfo } from './types'

export const settingsApi = {
  get: () => apiClient.get('/settings').then((r) => r.data),

  updateNotifications: (url: string) =>
    apiClient.put('/settings/notifications', { teams_webhook_url: url }).then((r) => r.data),

  testNotifications: () =>
    apiClient.post('/settings/notifications/test').then((r) => r.data),

  getDatabaseInfo: () =>
    apiClient.get<DBTableInfo[]>('/settings/database').then((r) => r.data),

  getLogs: (lines = 500) =>
    apiClient.get<{ lines: number; data: string[] }>('/settings/logs', { params: { lines } }).then((r) => r.data),

  updateJobInterval: (jobId: string, seconds: number) =>
    apiClient.put(`/scheduler/jobs/${encodeURIComponent(jobId)}/interval`, { seconds }).then((r) => r.data),
}
