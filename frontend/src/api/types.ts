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
  teams_notified?: string
  snow_ticket?: string
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
