import { apiClient } from './client'
import type { Host } from './types'

export const hostsApi = {
  list: (params?: { array_name?: string; search?: string }) =>
    apiClient.get<Host[]>('/hosts', { params }).then((r) => r.data),

  groups: (array_name?: string) =>
    apiClient.get('/hosts/groups', { params: array_name ? { array_name } : {} }).then((r) => r.data),
}
