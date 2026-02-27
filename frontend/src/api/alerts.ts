import { apiClient } from './client'
import type { Alert, Severity } from './types'

export const alertsApi = {
  list: (params?: { array_name?: string; severity?: Severity; resolved?: boolean; limit?: number }) =>
    apiClient.get<Alert[]>('/alerts', { params }).then((r) => r.data),
}
