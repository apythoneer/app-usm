import { apiClient } from './client'
import type { Alert, Severity, PaginatedResponse } from './types'

export const alertsApi = {
  list: (params?: {
    array_name?: string
    severity?: Severity
    vendor?: string
    resolved?: boolean
    limit?: number
    offset?: number
    sort_by?: string
    sort_dir?: string
  }) =>
    apiClient.get<PaginatedResponse<Alert>>('/alerts', { params }).then((r) => r.data),
}
