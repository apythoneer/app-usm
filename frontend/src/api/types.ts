// Vendor-agnostic API types — mirror backend Pydantic schemas

export type Vendor = 'pure' | 'netapp' | 'commvault' | 'dell' | 'hpe' | 'unknown'
export type Severity = 'critical' | 'warning' | 'info' | 'unknown'

export interface ArraySummary {
  array_name: string
  vendor: Vendor
  model?: string
  capacity_used_pct?: number
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
  read_latency_us?: number
  write_latency_us?: number
  read_bandwidth_bytes?: number
  write_bandwidth_bytes?: number
  capacity_total_bytes?: number
  capacity_used_bytes?: number
  capacity_used_pct?: number
  data_reduction?: number
  total_reduction?: number
  shared_space_bytes?: number
  snapshot_space_bytes?: number
  volume_space_bytes?: number
  controller_status?: string
  uptime_seconds?: number
  uptime_str?: string
  metadata?: Record<string, unknown>
}

export interface Volume {
  array_name: string
  vendor: Vendor
  volume_name: string
  size_bytes?: number
  used_bytes?: number
  data_reduction?: number
  total_reduction?: number
  snapshots?: number
  created?: string
  serial?: string
  hosts?: string[]
  host_groups?: string[]
  protection_groups?: string[]
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
