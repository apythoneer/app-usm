// Vendor-agnostic API types — mirror backend Pydantic schemas

export type Vendor = 'pure' | 'netapp' | 'hpe' | 'oracle' | 'hitachi' | 'commvault' | 'dell' | 'veeam' | 'nimble' | 'unknown'
export type Severity = 'critical' | 'warning' | 'info' | 'unknown'

export interface ArraySummary {
  array_name: string
  vendor: Vendor
  model?: string
  group?: string               // cloud/site label from arrays.txt (aws | azure | gcp | on-prem | etc.)
  capacity_total_bytes?: number
  capacity_used_pct?: number
  data_reduction?: number
  total_iops?: number
  read_latency_us?: number
  write_latency_us?: number
  array_status?: string
  collected_at?: string
}

export interface ArrayMetrics extends ArraySummary {
  firmware_version?: string
  read_iops?: number
  write_iops?: number
  read_bandwidth_bytes?: number
  write_bandwidth_bytes?: number
  capacity_used_bytes?: number
  total_reduction?: number
  shared_space_bytes?: number
  snapshot_space_bytes?: number
  volume_space_bytes?: number
  controller_status?: string
  uptime_seconds?: number
  uptime_str?: string
  metadata?: Record<string, unknown>
}

export interface ArrayTableRow {
  array_name: string
  vendor: Vendor
  model?: string
  group?: string
  array_status?: string
  active_alert_count: number
  capacity_used_bytes?: number
  capacity_total_bytes?: number   // usable (presented) capacity
  snapshot_space_bytes?: number
  capacity_used_pct?: number
  data_reduction?: number
  total_volumes: number
  total_hosts: number
  collected_at?: string
}

export interface FleetStats {
  total_arrays: number
  total_capacity_tb?: number
  total_used_tb?: number
  avg_utilization_pct?: number
  total_iops?: number
  avg_read_latency_us?: number
  avg_write_latency_us?: number
  avg_data_reduction?: number
  active_alerts: number
  total_volumes: number
  total_hosts: number
}

export interface Volume {
  array_name: string
  vendor: Vendor
  volume_name: string
  size_bytes?: number
  used_bytes?: number
  data_reduction?: number
  total_reduction?: number
  thin_provisioning?: number
  snapshots?: number
  created?: string
  serial?: string
  hosts?: string[]
  host_groups?: string[]
  protection_groups?: string[]
  notes?: string
  last_updated?: string
}

export interface Host {
  array_name: string
  vendor: Vendor
  host_name: string
  iqn?: string
  wwn?: string
  nqn?: string
  host_group?: string
  volumes?: string[]
  last_updated?: string
}

export interface Alert {
  id?: number
  array_name: string
  vendor: Vendor
  message_id?: number
  event?: string
  severity: Severity
  component_type?: string
  component_name?: string
  opened?: string
  closed?: string
  expected?: string
  actual?: string
  teams_notified?: string
  snow_ticket?: string
  datadog_notified?: string
  datadog_event_id?: string
  datadog_event_url?: string
  occurrence_count?: number
  event_occurrences?: number
  first_seen?: string
  last_seen?: string
  suppressed: boolean
  resolved: boolean
  collected_at?: string
}

export interface MetricsHistoryPoint {
  array_name: string
  collected_at: string
  read_iops?: number
  write_iops?: number
  read_latency_us?: number
  write_latency_us?: number
  capacity_used_pct?: number
}

export interface FleetHistoryResponse {
  hours: number
  arrays: string[]
  data_points: number
  data: MetricsHistoryPoint[]
}

export interface DBTableInfo {
  table_name: string
  row_count: number | null
  last_updated: string | null
  error?: string
}

export interface ManagedArray {
  id?: number
  array_name: string
  vendor: Vendor
  group_label?: string
  cred_key?: string
  enabled: boolean
  // DimStorageFinance fields
  array_fqdn?: string
  array_serial?: string
  model?: string
  site?: string
  technology?: string
  category?: string
  usage_label?: string
  disposition?: string
  oem?: string
  support_provider?: string
  install_date?: string
  eosl_date?: string
  maint_end_date?: string
  mgmt_ip?: string
  monitoring_status?: string
  dim_sync_at?: string
  created_at?: string
  updated_at?: string
}

export interface ManagedArrayCreate {
  array_name: string
  vendor: Vendor
  group_label?: string
  cred_key?: string
}

export interface ArrayVerifyResult {
  array_name: string
  keepass_ok: boolean
  connectivity_ok: boolean
  version?: string
  error?: string
}

export interface PaginatedResponse<T> {
  total: number
  limit: number
  offset: number
  data: T[]
}

// ── Capacity breakdown (usable/used/allocated by vendor & cloud) ──────────────

export interface CapacityBucket {
  arrays: number
  usable_tb: number
  used_tb: number
  free_tb: number
  allocated_tb: number
  utilization_pct: number
}

export interface VendorBucket extends CapacityBucket { vendor: string }
export interface CloudBucket extends CapacityBucket { cloud: string }
export interface VendorCloudBucket extends CapacityBucket { vendor: string; cloud: string }

export interface CapacityBreakdown {
  fleet: CapacityBucket
  by_vendor: VendorBucket[]
  by_cloud: CloudBucket[]
  by_vendor_cloud: VendorCloudBucket[]
}

// ── Per-array YTD / trailing growth ───────────────────────────────────────────

export interface GrowthTrendPoint {
  date: string
  usable_tb: number
  used_tb: number
  used_pct?: number
}

export interface ArrayGrowth {
  array_name: string
  months: number
  current: {
    usable_tb?: number
    used_tb?: number
    used_pct?: number
    collected_at?: string
  }
  ytd: {
    start_date: string
    start_used_tb: number
    current_used_tb: number
    growth_tb: number
    growth_pct?: number
  } | null
  trend: GrowthTrendPoint[]
}

// ── Capacity forecast ("how full will X be by <date>") ────────────────────────

export interface CapacityForecast {
  scope: string
  array_name: string | null
  window_days: number
  data_points: number
  current_used_tb: number
  usable_tb: number
  current_pct: number | null
  rate_tb_per_day: number
  trend: 'growing' | 'declining' | 'stable'
  days_to_full: number | null
  projected_full_date: string | null
  target_date: string | null
  projected_used_tb: number | null
  projected_pct: number | null
  projected_change_tb: number | null
  caveat: string | null
  error: string | null
}

// ── Fleet capacity daily trend (backed by daily_stats) ────────────────────────

export interface DailyTrendPoint {
  date: string
  total_capacity_tb: number
  total_used_tb: number
  avg_utilization_pct: number
  avg_data_reduction: number
  total_arrays: number
}

export interface DailyTrendProjection {
  span_days: number
  window_days?: number   // trailing window the forward projection is fit over
  net_change_tb: number
  net_change_pct: number | null
  avg_rate_tb_per_day: number
  headroom_tb: number
  usable_tb: number
  days_to_full: number | null
  projected_full_date: string | null
  trend: 'growing' | 'declining' | 'stable'
}

export interface DailyTrendResponse {
  days: number
  data_points: number
  data: DailyTrendPoint[]
  last_collected?: string | null
  projection?: DailyTrendProjection
}

// ── Top growers / shrinkers (ranked by capacity-used delta) ───────────────────

export interface TopGrower {
  array_name: string
  vendor: string
  start_date: string
  start_used_tb: number
  current_used_tb: number
  delta_tb: number
  delta_pct?: number | null
  utilization_pct?: number | null
}

export interface CapacityHistoryArray {
  array_name: string
  vendor: string
  group: string
  model: string
}
export interface CapacityHistoryPoint {
  d: string        // YYYY-MM-DD
  a: string        // array_name
  used_tb: number
  total_tb: number
  used_pct: number
}
export interface CapacityHistoryResponse {
  days: number
  arrays: CapacityHistoryArray[]
  points: CapacityHistoryPoint[]
}

export interface TopGrowersResponse {
  days: number
  count: number
  data: TopGrower[]
}

// ── Per-volume growth (backed by volumes_history) ─────────────────────────────

export interface VolumeHistoryCoverage {
  first_seen: string | null
  last_seen: string | null
  rows_total: number
  distinct_days: number
}

export interface VolumeGrowthPoint {
  date: string
  size_tb: number
  used_tb: number
  data_reduction: number
  snapshots: number
}

export interface VolumeGrowth {
  array_name: string
  volume_name: string
  days: number
  data_points: number
  growth: {
    start_date: string
    start_used_tb: number
    current_used_tb: number
    growth_tb: number
    growth_pct?: number | null
  } | null
  trend: VolumeGrowthPoint[]
}

export interface TopVolumeGrower {
  array_name: string
  vendor: string
  volume_name: string
  start_date: string
  start_used_tb: number
  current_used_tb: number
  delta_tb: number
  delta_pct?: number | null
}

export interface TopVolumeGrowersResponse {
  days: number
  array_name: string | null
  count: number
  data: TopVolumeGrower[]
}



