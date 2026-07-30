import { apiClient } from './client'
import type { DBTableInfo, ManagedArray, ManagedArrayCreate, ArrayVerifyResult } from './types'

export const managedArraysApi = {
  list: () =>
    apiClient.get<ManagedArray[]>('/arrays/managed').then((r) => r.data),

  add: (data: ManagedArrayCreate) =>
    apiClient.post<ManagedArray>('/arrays/managed', data).then((r) => r.data),

  update: (name: string, data: { vendor?: string; group_label?: string; cred_key?: string; enabled?: boolean; array_fqdn?: string; mgmt_ip?: string; monitoring_status?: string }) =>
    apiClient.put<ManagedArray>(`/arrays/managed/${encodeURIComponent(name)}`, data).then((r) => r.data),

  remove: (name: string) =>
    apiClient.delete(`/arrays/managed/${encodeURIComponent(name)}`).then((r) => r.data),

  verify: (name: string) =>
    apiClient.post<ArrayVerifyResult>(`/arrays/managed/${encodeURIComponent(name)}/verify`).then((r) => r.data),
}

export const settingsApi = {
  get: () => apiClient.get('/settings').then((r) => r.data),

  updateNotifications: (url: string) =>
    apiClient.put('/settings/notifications', { teams_webhook_url: url }).then((r) => r.data),

  testNotifications: () =>
    apiClient.post('/settings/notifications/test').then((r) => r.data),

  getDatadogPaging: () =>
    apiClient.get<{
      integration_configured: boolean
      runtime_enabled: boolean
      paging_active: boolean
      since: string | null
      notify_groups: string
    }>('/settings/datadog-paging').then((r) => r.data),

  setDatadogPaging: (enabled: boolean) =>
    apiClient.post('/settings/datadog-paging', { enabled }).then((r) => r.data),

  getDatabaseInfo: () =>
    apiClient.get<DBTableInfo[]>('/settings/database').then((r) => r.data),

  getLogs: (lines = 500) =>
    apiClient.get<{ lines: number; data: string[] }>('/settings/logs', { params: { lines } }).then((r) => r.data),

  updateJobInterval: (jobId: string, seconds: number) =>
    apiClient.put(`/scheduler/jobs/${encodeURIComponent(jobId)}/interval`, { seconds }).then((r) => r.data),

  getKeePassEntries: () =>
    apiClient.get<{ groups: Record<string, string[]>; total_entries: number }>('/settings/keepass-entries').then((r) => r.data),

  triggerInventorySync: () =>
    apiClient.post('/settings/inventory-sync').then((r) => r.data),

  getInventorySummary: () =>
    apiClient.get('/settings/inventory-summary').then((r) => r.data),
}
