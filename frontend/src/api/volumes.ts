import { apiClient } from './client'
import type { Volume, PaginatedResponse } from './types'

export const volumesApi = {
  list: (params?: { array_name?: string; search?: string; vendor?: string; limit?: number; offset?: number; sort_by?: string; sort_dir?: string }) =>
    apiClient.get<PaginatedResponse<Volume>>('/volumes', { params }).then((r) => r.data),

  updateNotes: (arrayName: string, volumeName: string, notes: string | null) =>
    apiClient
      .patch('/volumes/notes', { notes }, { params: { array_name: arrayName, volume_name: volumeName } })
      .then((r) => r.data),
}
