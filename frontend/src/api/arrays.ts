import { apiClient } from './client'
import type {
  ArraySummary, ArrayMetrics, ArrayTableRow, FleetStats, FleetHistoryResponse,
  CapacityBreakdown, ArrayGrowth, DailyTrendResponse, TopGrowersResponse, CapacityHistoryResponse,
  VolumeHistoryCoverage, VolumeGrowth, TopVolumeGrowersResponse, CapacityForecast,
} from './types'



export const arraysApi = {
  list: (vendor?: string) =>
    apiClient.get<ArraySummary[]>('/arrays', { params: vendor ? { vendor } : {} }).then((r) => r.data),

  get: (arrayName: string) =>
    apiClient.get<ArrayMetrics>(`/arrays/${encodeURIComponent(arrayName)}`).then((r) => r.data),

  fleetStats: () =>
    apiClient.get<FleetStats>('/arrays/fleet-stats').then((r) => r.data),

  // Enriched per-array rows (metrics + alert/volume/host counts) for the Arrays table.
  table: () =>
    apiClient.get<ArrayTableRow[]>('/arrays/table').then((r) => r.data),

  // Project used capacity forward for an array (or fleet if array omitted).
  forecast: (array?: string, targetDate?: string, window = 90) =>
    apiClient
      .get<CapacityForecast>('/analytics/forecast', {
        params: {
          ...(array ? { array } : {}),
          ...(targetDate ? { target_date: targetDate } : {}),
          window,
        },
      })
      .then((r) => r.data),

  history: (arrayName: string, hours = 24) =>
    apiClient
      .get(`/analytics/history/${encodeURIComponent(arrayName)}`, { params: { hours } })
      .then((r) => r.data),

  fleetHistory: (hours = 24) =>
    apiClient
      .get<FleetHistoryResponse>('/analytics/fleet-history', { params: { hours } })
      .then((r) => r.data),

  capacityBreakdown: () =>
    apiClient.get<CapacityBreakdown>('/analytics/capacity-breakdown').then((r) => r.data),

  arrayGrowth: (arrayName: string, months = 12) =>
    apiClient
      .get<ArrayGrowth>(`/analytics/array-growth/${encodeURIComponent(arrayName)}`, { params: { months } })
      .then((r) => r.data),

  dailyTrend: (days = 90) =>
    apiClient
      .get<DailyTrendResponse>('/analytics/daily-trend', { params: { days } })
      .then((r) => r.data),

  topGrowers: (days = 90, limit = 20) =>
    apiClient
      .get<TopGrowersResponse>('/analytics/top-growers', { params: { days, limit } })
      .then((r) => r.data),

  capacityHistory: (days = 90) =>
    apiClient
      .get<CapacityHistoryResponse>('/analytics/capacity-history', { params: { days } })
      .then((r) => r.data),

  // ── Per-volume growth (backed by volumes_history) ───────────────────────────
  volumeHistoryCoverage: () =>
    apiClient
      .get<VolumeHistoryCoverage>('/analytics/volume-history-coverage')
      .then((r) => r.data),

  volumeGrowth: (arrayName: string, volumeName: string, days = 90) =>
    apiClient
      .get<VolumeGrowth>(
        `/analytics/volume-growth/${encodeURIComponent(arrayName)}/${encodeURIComponent(volumeName)}`,
        { params: { days } },
      )
      .then((r) => r.data),

  topVolumeGrowers: (days = 90, limit = 25, arrayName?: string) =>
    apiClient
      .get<TopVolumeGrowersResponse>('/analytics/top-volume-growers', {
        params: { days, limit, ...(arrayName ? { array_name: arrayName } : {}) },
      })
      .then((r) => r.data),



  // Download the multi-sheet capacity Excel report and trigger a browser save.
  exportCapacityXlsx: async () => {
    const res = await apiClient.get('/analytics/capacity-export.xlsx', { responseType: 'blob' })
    const disposition = (res.headers?.['content-disposition'] as string) || ''
    const match = disposition.match(/filename="?([^"]+)"?/)
    const filename = match ? match[1] : 'usm_capacity.xlsx'
    const url = window.URL.createObjectURL(new Blob([res.data]))
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.URL.revokeObjectURL(url)
  },
}


