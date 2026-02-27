import { apiClient } from './client'
import type { Volume } from './types'

export const volumesApi = {
  list: (params?: { array_name?: string; search?: string; limit?: number }) =>
    apiClient.get<Volume[]>('/volumes', { params }).then((r) => r.data),

  updateNotes: (arrayName: string, volumeName: string, notes: string | null) =>
    apiClient
      .patch(`/volumes/${encodeURIComponent(arrayName)}/${encodeURIComponent(volumeName)}/notes`, { notes })
      .then((r) => r.data),
}
