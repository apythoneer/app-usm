import { useState, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Server, AlertTriangle, Database, Activity, Zap, Clock,
  BarChart2, Users, ChevronDown, ChevronRight, ChevronUp, X, HardDrive,
  Cloud, Building2, Search, Filter,
} from 'lucide-react'
import ErrorState from '@/components/common/ErrorState'
import VendorBadge from '@/components/common/VendorBadge'
import { arraysApi } from '@/api/arrays'
import { alertsApi } from '@/api/alerts'
import { volumesApi } from '@/api/volumes'
import { hostsApi } from '@/api/hosts'
import type { ArraySummary, ArrayMetrics, ArrayTableRow, Alert, FleetStats } from '@/api/types'
import {
  formatBytes, formatTB, formatIOPS, formatLatency, formatPct,
  formatReduction, severityBg, usedPctColor
} from '@/utils/formatters'

// ── Cloud provider icons ─────────────────────────────────────────────────────

const PROVIDER_ICONS: Record<string, string> = {
  aws: '☁️',
  azure: '🔷',
  gcp: '🟡',
  other: '☁️',
}

// Human-friendly cloud provider labels for filter dropdown
const PROVIDER_LABELS: Record<string, string> = {
  aws: 'AWS',
  azure: 'Azure',
  gcp: 'GCP',
  other: 'Other Cloud',
}


// ── Deployment type helpers ──────────────────────────────────────────────────

function isCloudGroup(group?: string | null): boolean {
  if (!group) return false
  const g = group.toLowerCase()
  return g.startsWith('cloud-') || g === 'aws' || g === 'azure' || g === 'gcp'
}

function getCloudProvider(group?: string | null): string {
  if (!group) return 'other'
  const g = group.toLowerCase()
  if (g.startsWith('cloud-aws') || g === 'aws') return 'aws'
  if (g.startsWith('cloud-azu') || g === 'azure') return 'azure'
  if (g.startsWith('cloud-gcp') || g === 'gcp') return 'gcp'
  if (g.startsWith('cloud-')) return 'other'
  return 'other'
}

function getDCCode(group?: string | null): string {
  if (!group) return 'Unknown'
  return group  // Return the raw DC code (ODC, IDC, DDC, etc.)
}

// ── Fleet stat card (clickable) ──────────────────────────────────────────────

function StatCard({ icon: Icon, label, value, sub, color = 'text-brand-400', onClick }: {
  icon: React.ElementType; label: string; value: string; sub?: string; color?: string; onClick?: () => void
}) {
  return (
    <div
      className={`card flex items-start gap-3 p-4 transition-all duration-150 ${onClick ? 'cursor-pointer hover:border-gray-600 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/20' : ''}`}
      onClick={onClick}
    >
      <div className={`mt-0.5 ${color}`}><Icon size={20} /></div>
      <div className="min-w-0">
        <p className="text-xs text-gray-500 uppercase tracking-wide truncate">{label}</p>
        <p className="text-xl font-semibold text-white">{value}</p>
        {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}

// ── Array drilldown modal ─────────────────────────────────────────────────────

type DrilldownTab = 'overview' | 'volumes' | 'hosts'

function ArrayModal({ arrayName, initialTab = 'overview', onClose }: {
  arrayName: string; initialTab?: DrilldownTab; onClose: () => void
}) {
  const [tab, setTab] = useState<DrilldownTab>(initialTab)

  const { data, isLoading } = useQuery<ArrayMetrics>({
    queryKey: ['array', arrayName],
    queryFn: () => arraysApi.get(arrayName),
  })

  const { data: volResult } = useQuery({
    queryKey: ['volumes', 'drilldown', arrayName],
    queryFn: () => volumesApi.list({ array_name: arrayName, limit: 200 }),
    enabled: tab === 'volumes',
  })

  const { data: hostResult } = useQuery({
    queryKey: ['hosts', 'drilldown', arrayName],
    queryFn: () => hostsApi.list({ array_name: arrayName, limit: 200 }),
    enabled: tab === 'hosts',
  })

  const volumes = volResult?.data ?? []
  const hosts = hostResult?.data ?? []

  const tabs: { key: DrilldownTab; label: string; count?: number }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'volumes', label: 'Volumes', count: volResult?.total },
    { key: 'hosts', label: 'Hosts', count: hostResult?.total },
  ]

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-3xl mx-4 max-h-[90vh] overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div>
            <h2 className="text-white font-semibold text-lg truncate">{arrayName}</h2>
            {data && <p className="text-xs text-gray-500 mt-0.5">{data.vendor} · {data.model || 'Unknown model'}</p>}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white p-1">
            <X size={18} />
          </button>
        </div>

        {/* Tab bar */}
        <div className="flex gap-1 px-4 pt-3 border-b border-gray-800">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2 text-xs font-medium rounded-t-lg transition-colors ${
                tab === t.key
                  ? 'bg-gray-800 text-white border border-gray-700 border-b-0'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {t.label}
              {t.count != null && <span className="ml-1 text-gray-600">({t.count})</span>}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="p-4">
          {isLoading ? (
            <p className="text-gray-500 text-sm py-4 text-center">Loading…</p>
          ) : !data ? (
            <p className="text-gray-500 text-sm py-4 text-center">No data available</p>
          ) : tab === 'overview' ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Identity */}
              <div className="col-span-2 bg-gray-800 rounded-lg p-3">
                <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Identity</h4>
                <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
                  <dt className="text-gray-500">Array</dt><dd className="text-gray-200 font-mono text-xs">{data.array_name}</dd>
                  <dt className="text-gray-500">Vendor</dt><dd className="text-gray-200 capitalize">{data.vendor}</dd>
                  <dt className="text-gray-500">Model</dt><dd className="text-gray-200">{data.model || '—'}</dd>
                  <dt className="text-gray-500">Firmware</dt><dd className="text-gray-200">{data.firmware_version || '—'}</dd>
                  <dt className="text-gray-500">Status</dt><dd className="text-gray-200 capitalize">{data.array_status || '—'}</dd>
                  <dt className="text-gray-500">Uptime</dt><dd className="text-gray-200">{data.uptime_str || '—'}</dd>
                </dl>
              </div>
              {/* Performance */}
              <div className="bg-gray-800 rounded-lg p-3">
                <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Performance</h4>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
                  <dt className="text-gray-500">Read IOPS</dt><dd className="text-gray-200">{formatIOPS(data.read_iops)}</dd>
                  <dt className="text-gray-500">Write IOPS</dt><dd className="text-gray-200">{formatIOPS(data.write_iops)}</dd>
                  <dt className="text-gray-500">Read Latency</dt><dd className="text-gray-200">{formatLatency(data.read_latency_us)}</dd>
                  <dt className="text-gray-500">Write Latency</dt><dd className="text-gray-200">{formatLatency(data.write_latency_us)}</dd>
                </dl>
              </div>
              {/* Capacity */}
              <div className="bg-gray-800 rounded-lg p-3">
                <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Capacity</h4>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
                  <dt className="text-gray-500">Total</dt><dd className="text-gray-200">{formatBytes(data.capacity_total_bytes)}</dd>
                  <dt className="text-gray-500">Used</dt><dd className="text-gray-200">{formatBytes(data.capacity_used_bytes)}</dd>
                  <dt className="text-gray-500">Utilization</dt><dd className="text-gray-200">{formatPct(data.capacity_used_pct)}</dd>
                  <dt className="text-gray-500">Data Reduction</dt><dd className="text-gray-200">{formatReduction(data.data_reduction)}</dd>
                  <dt className="text-gray-500">Snapshots</dt><dd className="text-gray-200">{formatBytes(data.snapshot_space_bytes)}</dd>
                  <dt className="text-gray-500">Volumes</dt><dd className="text-gray-200">{formatBytes(data.volume_space_bytes)}</dd>
                </dl>
              </div>
            </div>
          ) : tab === 'volumes' ? (
            <div>
              {volumes.length === 0 ? (
                <p className="text-gray-500 text-sm text-center py-4">No volumes found</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-700/50 bg-gray-800/40">
                        <th className="px-3 py-1.5 text-left text-xs text-gray-500 uppercase">Volume</th>
                        <th className="px-3 py-1.5 text-right text-xs text-gray-500 uppercase">Size</th>
                        <th className="px-3 py-1.5 text-right text-xs text-gray-500 uppercase">Used</th>
                        <th className="px-3 py-1.5 text-right text-xs text-gray-500 uppercase">Reduction</th>
                        <th className="px-3 py-1.5 text-left text-xs text-gray-500 uppercase">Hosts</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-800/50">
                      {volumes.map((v) => (
                        <tr key={v.volume_name} className="hover:bg-gray-800/30">
                          <td className="px-3 py-1.5 text-xs text-gray-200 font-mono truncate max-w-[200px]">{v.volume_name}</td>
                          <td className="px-3 py-1.5 text-xs text-gray-300 text-right">{formatBytes(v.size_bytes)}</td>
                          <td className="px-3 py-1.5 text-xs text-gray-300 text-right">{formatBytes(v.used_bytes)}</td>
                          <td className="px-3 py-1.5 text-xs text-gray-300 text-right">{formatReduction(v.data_reduction)}</td>
                          <td className="px-3 py-1.5 text-xs text-gray-500 truncate max-w-[150px]">
                            {(v.hosts ?? []).slice(0, 2).join(', ') || '—'}
                            {(v.hosts ?? []).length > 2 && ` +${(v.hosts ?? []).length - 2}`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : (
            /* hosts tab */
            <div>
              {hosts.length === 0 ? (
                <p className="text-gray-500 text-sm text-center py-4">No hosts found</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-700/50 bg-gray-800/40">
                        <th className="px-3 py-1.5 text-left text-xs text-gray-500 uppercase">Host</th>
                        <th className="px-3 py-1.5 text-left text-xs text-gray-500 uppercase">Group</th>
                        <th className="px-3 py-1.5 text-left text-xs text-gray-500 uppercase">WWN</th>
                        <th className="px-3 py-1.5 text-right text-xs text-gray-500 uppercase">Volumes</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-800/50">
                      {hosts.map((h) => (
                        <tr key={h.host_name} className="hover:bg-gray-800/30">
                          <td className="px-3 py-1.5 text-xs text-gray-200 font-mono">{h.host_name}</td>
                          <td className="px-3 py-1.5 text-xs text-gray-400">{h.host_group || '—'}</td>
                          <td className="px-3 py-1.5 text-xs text-gray-500 font-mono truncate max-w-[200px]">{h.wwn || h.iqn || '—'}</td>
                          <td className="px-3 py-1.5 text-xs text-gray-400 text-right">{(h.volumes ?? []).length}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Array row ─────────────────────────────────────────────────────────────────

function ArrayRow({ array, onDblClick }: { array: ArraySummary; onDblClick: () => void }) {
  const statusOk = array.array_status === 'ok' || array.array_status === 'normal'
  return (
    <tr className="hover:bg-gray-800/40 cursor-pointer select-none transition-colors" onDoubleClick={onDblClick}>
      <td className="px-4 py-2.5 text-sm text-white">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusOk ? 'bg-green-400' : array.array_status ? 'bg-red-400' : 'bg-gray-600'}`} />
          <span className="truncate max-w-xs font-mono text-xs">{array.array_name}</span>
        </div>
      </td>
      <td className="px-4 py-2.5 text-sm"><VendorBadge vendor={array.vendor} /></td>
      <td className="px-4 py-2.5 text-sm text-gray-400">{array.model || '—'}</td>
      <td className="px-4 py-2.5 text-sm">
        <span className={`capitalize text-xs px-2 py-0.5 rounded-full border ${statusOk
          ? 'bg-green-500/10 text-green-400 border-green-500/20'
          : array.array_status
          ? 'bg-red-500/10 text-red-400 border-red-500/20'
          : 'bg-gray-500/10 text-gray-500 border-gray-500/20'
        }`}>
          {array.array_status || 'unknown'}
        </span>
      </td>
      <td className="px-4 py-2.5 text-sm text-gray-300 text-right font-mono">{formatIOPS(array.total_iops)}</td>
      <td className="px-4 py-2.5 text-sm text-gray-300 text-right font-mono">
        {formatLatency(array.read_latency_us)} / {formatLatency(array.write_latency_us)}
      </td>
      <td className="px-4 py-2.5 text-sm text-right">
        <div className="flex items-center gap-2 justify-end">
          <span className={
            array.capacity_used_pct != null && array.capacity_used_pct >= 90
              ? 'text-red-400 font-medium'
              : array.capacity_used_pct != null && array.capacity_used_pct >= 75
              ? 'text-yellow-400'
              : 'text-gray-300'
          }>
            {formatPct(array.capacity_used_pct)}
          </span>
          <div className="w-16 h-1.5 bg-gray-700 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${usedPctColor(array.capacity_used_pct)}`}
              style={{ width: `${Math.min(array.capacity_used_pct ?? 0, 100)}%` }}
            />
          </div>
        </div>
      </td>
      <td className="px-4 py-2.5 text-sm text-gray-300 text-right font-mono">{formatBytes(array.capacity_total_bytes)}</td>
    </tr>
  )
}

// ── Array table (shared between sections) ─────────────────────────────────────

function ArrayTable({ arrays, onDblClick }: { arrays: ArraySummary[]; onDblClick: (name: string) => void }) {
  return (
    <div className="border border-gray-700/50 rounded-lg overflow-hidden">
      <table className="w-full">
        <thead>
          <tr className="border-b border-gray-700/50 bg-gray-800/40">
            <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Array</th>
            <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Vendor</th>
            <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Model</th>
            <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Status</th>
            <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">IOPS</th>
            <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Latency R/W</th>
            <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Used</th>
            <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Total</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700/30">
          {arrays.map((a) => (
            <ArrayRow key={a.array_name} array={a} onDblClick={() => onDblClick(a.array_name)} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Cloud provider sub-group ──────────────────────────────────────────────────

function CloudProviderSection({ provider, arrays, onDblClick }: {
  provider: string; arrays: ArraySummary[]; onDblClick: (name: string) => void
}) {
  const [open, setOpen] = useState(true)
  const icon = PROVIDER_ICONS[provider] ?? '☁️'
  const displayName = provider.toUpperCase()

  return (
    <div className="ml-4 mb-3">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-1.5 bg-gray-800/40 hover:bg-gray-800/60 rounded-lg text-left transition-colors border border-gray-700/30"
      >
        {open
          ? <ChevronDown size={13} className="text-gray-400 flex-shrink-0" />
          : <ChevronRight size={13} className="text-gray-400 flex-shrink-0" />}
        <span>{icon}</span>
        <span className="text-xs font-medium text-gray-300">{displayName}</span>
        <span className="text-xs text-gray-500">({arrays.length})</span>
      </button>
      {open && (
        <div className="mt-1">
          <ArrayTable arrays={arrays} onDblClick={onDblClick} />
        </div>
      )}
    </div>
  )
}

// ── Deployment type section (On-Prem / Cloud) ─────────────────────────────────

function DeploymentSection({ type, arrays, onDblClick }: {
  type: 'on-prem' | 'cloud'
  arrays: ArraySummary[]
  onDblClick: (name: string) => void
}) {
  const [open, setOpen] = useState(true)
  const isCloud = type === 'cloud'

  // For cloud, group by provider. For on-prem, group by DC.
  const subGroups = useMemo(() => {
    if (isCloud) {
      return arrays.reduce<Record<string, ArraySummary[]>>((acc, arr) => {
        const provider = getCloudProvider(arr.group)
        ;(acc[provider] = acc[provider] || []).push(arr)
        return acc
      }, {})
    } else {
      return arrays.reduce<Record<string, ArraySummary[]>>((acc, arr) => {
        const dc = getDCCode(arr.group)
        ;(acc[dc] = acc[dc] || []).push(arr)
        return acc
      }, {})
    }
  }, [arrays, isCloud])

  const icon = isCloud ? Cloud : Building2
  const Icon = icon
  const label = isCloud ? 'Cloud' : 'On-Premises'
  const sublabel = Object.keys(subGroups).map(k => `${k}: ${subGroups[k].length}`).join(' · ')

  return (
    <div className="mb-4">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2.5 bg-gray-800/60 hover:bg-gray-800 rounded-lg text-left transition-colors border border-gray-700/50"
      >
        {open
          ? <ChevronDown size={15} className="text-gray-400 flex-shrink-0" />
          : <ChevronRight size={15} className="text-gray-400 flex-shrink-0" />}
        <Icon size={16} className={isCloud ? 'text-blue-400' : 'text-amber-400'} />
        <span className="text-sm font-semibold text-gray-200">{label}</span>
        <span className="text-xs text-gray-500 ml-1">({arrays.length})</span>
        <span className="text-xs text-gray-600 ml-auto">{sublabel}</span>
      </button>

      {open && (
        <div className="mt-2">
          {Object.entries(subGroups)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([key, groupArrays]) => (
              <CloudProviderSection
                key={key}
                provider={key}
                arrays={groupArrays as ArraySummary[]}
                onDblClick={onDblClick}
              />
            ))}
        </div>
      )}
    </div>
  )
}

// ── Arrays table modal ────────────────────────────────────────────────────────

type ArraySortField =
  | 'array_name' | 'vendor' | 'model' | 'array_status' | 'active_alert_count'
  | 'capacity_used_bytes' | 'capacity_total_bytes' | 'snapshot_space_bytes'
  | 'total_volumes' | 'total_hosts'

function SortHeader({ field, label, sortBy, sortDir, onSort, align = 'left' }: {
  field: ArraySortField; label: string; sortBy: ArraySortField | ''
  sortDir: 'asc' | 'desc'; onSort: (f: ArraySortField) => void
  align?: 'left' | 'right'
}) {
  const active = sortBy === field
  return (
    <th
      className={`px-3 py-2 text-xs text-gray-500 uppercase tracking-wide cursor-pointer select-none hover:text-gray-300 transition-colors ${align === 'right' ? 'text-right' : 'text-left'}`}
      onClick={() => onSort(field)}
    >
      <span className={`inline-flex items-center gap-1 ${align === 'right' ? 'flex-row-reverse' : ''}`}>
        {label}
        {active && (sortDir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />)}
      </span>
    </th>
  )
}

function statusClasses(status?: string): { dot: string; pill: string } {
  const ok = status === 'ok' || status === 'normal' || status === 'healthy'
  if (ok) return { dot: 'bg-green-400', pill: 'bg-green-500/10 text-green-400 border-green-500/20' }
  if (status) return { dot: 'bg-red-400', pill: 'bg-red-500/10 text-red-400 border-red-500/20' }
  return { dot: 'bg-gray-600', pill: 'bg-gray-500/10 text-gray-500 border-gray-500/20' }
}

function ArraysTableModal({ onClose, onSelectArray }: {
  onClose: () => void
  onSelectArray: (name: string, tab: DrilldownTab) => void
}) {
  const [search, setSearch] = useState('')
  const [vendorFilter, setVendorFilter] = useState('')
  const [cloudFilter, setCloudFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')   // '', 'healthy', 'issue'
  const [sortBy, setSortBy] = useState<ArraySortField | ''>('array_name')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  const { data: rows = [], isLoading, isError, error, refetch } = useQuery<ArrayTableRow[]>({
    queryKey: ['arrays-table'],
    queryFn: () => arraysApi.table(),
    refetchInterval: 60_000,
  })

  const vendors = useMemo(() => [...new Set(rows.map(r => r.vendor))].sort(), [rows])

  function handleSort(f: ArraySortField) {
    if (sortBy === f) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortBy(f); setSortDir('asc') }
  }

  const filtered = useMemo(() => {
    let result = rows
    if (vendorFilter) result = result.filter(r => r.vendor === vendorFilter)
    if (cloudFilter) {
      if (cloudFilter === 'on-prem') result = result.filter(r => !isCloudGroup(r.group))
      else if (cloudFilter === 'cloud') result = result.filter(r => isCloudGroup(r.group))
      else result = result.filter(r => isCloudGroup(r.group) && getCloudProvider(r.group) === cloudFilter)
    }
    if (statusFilter) {
      const isOk = (s?: string) => s === 'ok' || s === 'normal' || s === 'healthy'
      result = result.filter(r => statusFilter === 'healthy' ? isOk(r.array_status) : !isOk(r.array_status))
    }
    if (search) {
      const q = search.toLowerCase()
      result = result.filter(r =>
        r.array_name.toLowerCase().includes(q) || (r.model || '').toLowerCase().includes(q))
    }
    if (sortBy) {
      const dir = sortDir === 'asc' ? 1 : -1
      result = [...result].sort((a, b) => {
        const av = a[sortBy], bv = b[sortBy]
        if (av == null && bv == null) return 0
        if (av == null) return 1          // nulls last regardless of direction
        if (bv == null) return -1
        if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir
        return String(av).localeCompare(String(bv)) * dir
      })
    }
    return result
  }, [rows, vendorFilter, cloudFilter, statusFilter, search, sortBy, sortDir])

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center bg-black/60 backdrop-blur-sm p-4 animate-fade-in overflow-y-auto"
      onClick={onClose}
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-6xl my-6 shadow-2xl animate-scale-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div className="flex items-center gap-2">
            <HardDrive size={18} className="text-brand-400" />
            <h2 className="text-white font-semibold text-lg">Storage Arrays</h2>
            <span className="text-xs text-gray-500">
              {filtered.length}{filtered.length !== rows.length ? ` of ${rows.length}` : ''}
            </span>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white p-1"><X size={18} /></button>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-2 flex-wrap px-4 py-3 border-b border-gray-800">
          <div className="relative">
            <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500" />
            <input
              type="text" placeholder="Search name / model…"
              value={search} onChange={(e) => setSearch(e.target.value)}
              className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg pl-7 pr-3 py-1.5 focus:outline-none focus:border-brand-500 w-48"
            />
          </div>
          {vendors.length > 1 && (
            <div className="relative">
              <Filter size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500" />
              <select
                value={vendorFilter} onChange={(e) => setVendorFilter(e.target.value)}
                className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg pl-7 pr-3 py-1.5 focus:outline-none focus:border-brand-500 appearance-none cursor-pointer"
              >
                <option value="">All vendors</option>
                {vendors.map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>
          )}
          <div className="relative">
            <Cloud size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500" />
            <select
              value={cloudFilter} onChange={(e) => setCloudFilter(e.target.value)}
              className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg pl-7 pr-3 py-1.5 focus:outline-none focus:border-brand-500 appearance-none cursor-pointer"
            >
              <option value="">All deployments</option>
              <option value="on-prem">On-Premises</option>
              <option value="cloud">All Cloud</option>
              <option value="aws">AWS</option>
              <option value="azure">Azure</option>
              <option value="gcp">GCP</option>
              <option value="other">Other Cloud</option>
            </select>
          </div>
          <select
            value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500 appearance-none cursor-pointer"
          >
            <option value="">Any status</option>
            <option value="healthy">Healthy</option>
            <option value="issue">Needs attention</option>
          </select>
        </div>

        {/* Body */}
        <div className="p-4">
          {isError ? (
            <ErrorState what="arrays" error={error} onRetry={() => refetch()} />
          ) : isLoading ? (
            <p className="text-gray-500 text-sm py-6 text-center">Loading arrays…</p>
          ) : filtered.length === 0 ? (
            <p className="text-gray-500 text-sm py-6 text-center">No arrays match your filters.</p>
          ) : (
            <div className="overflow-x-auto border border-gray-700/50 rounded-lg">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-700/50 bg-gray-800/40">
                    <SortHeader field="array_name" label="Array" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader field="vendor" label="Vendor" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader field="model" label="Model" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader field="array_status" label="Status" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader field="active_alert_count" label="Alerts" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} align="right" />
                    <SortHeader field="capacity_used_bytes" label="Used" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} align="right" />
                    <SortHeader field="capacity_total_bytes" label="Usable" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} align="right" />
                    <SortHeader field="snapshot_space_bytes" label="Snapshot" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} align="right" />
                    <SortHeader field="total_volumes" label="Volumes" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} align="right" />
                    <SortHeader field="total_hosts" label="Hosts" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} align="right" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800/50">
                  {filtered.map((r) => {
                    const sc = statusClasses(r.array_status)
                    return (
                      <tr
                        key={r.array_name}
                        className="hover:bg-gray-800/40 cursor-pointer transition-colors"
                        onClick={() => onSelectArray(r.array_name, 'overview')}
                      >
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-2">
                            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${sc.dot}`} />
                            <span className="font-mono text-xs text-white truncate max-w-[200px]">{r.array_name}</span>
                          </div>
                        </td>
                        <td className="px-3 py-2"><VendorBadge vendor={r.vendor} /></td>
                        <td className="px-3 py-2 text-xs text-gray-400">{r.model || '—'}</td>
                        <td className="px-3 py-2">
                          <span className={`capitalize text-xs px-2 py-0.5 rounded-full border ${sc.pill}`}>
                            {r.array_status || 'unknown'}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right">
                          {r.active_alert_count > 0 ? (
                            <span className="text-xs font-medium text-red-400">{r.active_alert_count}</span>
                          ) : (
                            <span className="text-xs text-gray-600">0</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-xs text-gray-300 text-right font-mono">{formatBytes(r.capacity_used_bytes)}</td>
                        <td className="px-3 py-2 text-xs text-gray-300 text-right font-mono">{formatBytes(r.capacity_total_bytes)}</td>
                        <td className="px-3 py-2 text-xs text-gray-400 text-right font-mono">{formatBytes(r.snapshot_space_bytes)}</td>
                        <td
                          className="px-3 py-2 text-xs text-right font-mono text-brand-400 hover:text-brand-300"
                          onClick={(e) => { e.stopPropagation(); onSelectArray(r.array_name, 'volumes') }}
                        >
                          {r.total_volumes}
                        </td>
                        <td
                          className="px-3 py-2 text-xs text-right font-mono text-brand-400 hover:text-brand-300"
                          onClick={(e) => { e.stopPropagation(); onSelectArray(r.array_name, 'hosts') }}
                        >
                          {r.total_hosts}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-xs text-gray-600 mt-2">Click a row for details · click a Volumes/Hosts count to jump to that tab</p>
        </div>
      </div>
    </div>
  )
}

// ── Dashboard page ────────────────────────────────────────────────────────────

export default function Dashboard() {
  const navigate = useNavigate()
  const [selectedArray, setSelectedArray] = useState<string | null>(null)
  const [drilldownTab, setDrilldownTab] = useState<DrilldownTab>('overview')
  const [showArraysTable, setShowArraysTable] = useState(false)
  const [vendorFilter, setVendorFilter] = useState('')
  const [cloudFilter, setCloudFilter] = useState('')   // '', 'on-prem', 'aws', 'azure', 'gcp', 'other'
  const [searchFilter, setSearchFilter] = useState('')
  const arraysRef = useRef<HTMLDivElement>(null)

  function openDrilldown(name: string, tab: DrilldownTab = 'overview') {
    setDrilldownTab(tab)
    setSelectedArray(name)
  }


  const {
    data: arrays = [],
    isLoading: arraysLoading,
    isError: arraysError,
    error: arraysErrorObj,
    refetch: refetchArrays,
  } = useQuery<ArraySummary[]>({
    queryKey: ['arrays'],
    queryFn: () => arraysApi.list(),
    refetchInterval: 60_000,
  })

  const { data: fleet } = useQuery<FleetStats>({
    queryKey: ['fleet-stats'],
    queryFn: () => arraysApi.fleetStats(),
    refetchInterval: 60_000,
  })

  const { data: alertsResult } = useQuery({
    queryKey: ['alerts', 'active'],
    queryFn: () => alertsApi.list({ resolved: false, limit: 50 }),
    refetchInterval: 30_000,
  })
  const alerts: Alert[] = alertsResult?.data ?? []

  // Unique vendors for filter
  const vendors = useMemo(() => [...new Set(arrays.map(a => a.vendor))].sort(), [arrays])

  // Filter arrays
  const filteredArrays = useMemo(() => {
    let result = arrays
    if (vendorFilter) result = result.filter(a => a.vendor === vendorFilter)
    if (cloudFilter) {
      if (cloudFilter === 'on-prem') {
        result = result.filter(a => !isCloudGroup(a.group))
      } else if (cloudFilter === 'cloud') {
        result = result.filter(a => isCloudGroup(a.group))
      } else {
        // specific provider: aws / azure / gcp / other
        result = result.filter(a => isCloudGroup(a.group) && getCloudProvider(a.group) === cloudFilter)
      }
    }
    if (searchFilter) {
      const q = searchFilter.toLowerCase()
      result = result.filter(a =>
        a.array_name.toLowerCase().includes(q) ||
        (a.model || '').toLowerCase().includes(q)
      )
    }
    return result
  }, [arrays, vendorFilter, cloudFilter, searchFilter])


  // Split into On-Prem and Cloud
  const { onPremArrays, cloudArrays } = useMemo(() => {
    const onPrem: ArraySummary[] = []
    const cloud: ArraySummary[] = []
    filteredArrays.forEach(arr => {
      if (isCloudGroup(arr.group)) {
        cloud.push(arr)
      } else {
        onPrem.push(arr)
      }
    })
    return { onPremArrays: onPrem, cloudArrays: cloud }
  }, [filteredArrays])

  const criticalCount = alerts.filter((a) => a.severity === 'critical').length
  const warningCount  = alerts.filter((a) => a.severity === 'warning').length

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-white">Dashboard</h2>
        <p className="text-gray-400 mt-0.5 text-sm">Storage Intelligence Platform — fleet overview</p>
      </div>

      {/* Fleet stat cards — clickable */}
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
        <StatCard icon={Server} label="Arrays"
          value={String(fleet?.total_arrays ?? arrays.length)}
          sub="view all →"
          onClick={() => setShowArraysTable(true)} />
        <StatCard icon={Database} label="Total Capacity"
          value={formatTB(fleet?.total_capacity_tb)}
          sub="capacity →"
          onClick={() => navigate('/capacity')} />
        <StatCard icon={HardDrive} label="Used"
          value={formatTB(fleet?.total_used_tb)}
          sub={fleet?.avg_utilization_pct != null ? `${fleet.avg_utilization_pct.toFixed(1)}% avg` : undefined}
          onClick={() => navigate('/capacity')} />
        <StatCard icon={BarChart2} label="Data Reduction"
          value={fleet?.avg_data_reduction != null ? formatReduction(fleet.avg_data_reduction) : '—'}
          onClick={() => navigate('/analytics')} />
        <StatCard icon={Zap} label="Total IOPS"
          value={fleet?.total_iops != null ? formatIOPS(fleet.total_iops) : '—'}
          onClick={() => navigate('/analytics')} />
        <StatCard icon={Clock} label="Avg Latency (R)"
          value={fleet?.avg_read_latency_us != null ? formatLatency(fleet.avg_read_latency_us) : '—'}
          onClick={() => navigate('/analytics')} />
        <StatCard icon={Users} label="Hosts / Volumes"
          value={fleet?.total_hosts != null ? String(fleet.total_hosts) : '—'}
          sub={fleet?.total_volumes != null ? `${fleet.total_volumes} volumes` : undefined}
          onClick={() => navigate('/hosts')} />
        <StatCard icon={AlertTriangle} label="Active Alerts"
          value={String(fleet?.active_alerts ?? alerts.length)}
          sub={criticalCount > 0 ? `${criticalCount} critical` : warningCount > 0 ? `${warningCount} warning` : 'all clear'}
          color={criticalCount > 0 ? 'text-red-400' : warningCount > 0 ? 'text-yellow-400' : 'text-green-400'}
          onClick={() => navigate('/alerts')} />
      </div>

      {/* Arrays section */}
      <div ref={arraysRef}>
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h3 className="text-sm font-semibold text-gray-300">Storage Arrays</h3>
          <div className="flex items-center gap-2">
            {/* Vendor filter */}
            {vendors.length > 1 && (
              <div className="relative">
                <Filter size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500" />
                <select
                  value={vendorFilter}
                  onChange={(e) => setVendorFilter(e.target.value)}
                  className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg pl-7 pr-3 py-1.5 focus:outline-none focus:border-brand-500 appearance-none cursor-pointer"
                >
                  <option value="">All vendors</option>
                  {vendors.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </div>
            )}
            {/* Cloud provider / deployment filter */}
            <div className="relative">
              <Cloud size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500" />
              <select
                value={cloudFilter}
                onChange={(e) => setCloudFilter(e.target.value)}
                className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg pl-7 pr-3 py-1.5 focus:outline-none focus:border-brand-500 appearance-none cursor-pointer"
              >
                <option value="">All deployments</option>
                <option value="on-prem">On-Premises</option>
                <option value="cloud">All Cloud</option>
                <option value="aws">{PROVIDER_LABELS.aws}</option>
                <option value="azure">{PROVIDER_LABELS.azure}</option>
                <option value="gcp">{PROVIDER_LABELS.gcp}</option>
                <option value="other">{PROVIDER_LABELS.other}</option>
              </select>
            </div>
            {/* Search */}

            <div className="relative">
              <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="text"
                placeholder="Search arrays…"
                value={searchFilter}
                onChange={(e) => setSearchFilter(e.target.value)}
                className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg pl-7 pr-3 py-1.5 focus:outline-none focus:border-brand-500 w-40"
              />
            </div>
            <span className="text-xs text-gray-500">
              {filteredArrays.length} of {arrays.length} · Double-click for details
            </span>
          </div>
        </div>

        {arraysError ? (
          // Must be checked BEFORE the empty branch: a failed request leaves
          // `arrays` at its [] default, which previously fell through to
          // "No arrays found — check collectors are running", reporting a
          // backend outage as an empty fleet and blaming the wrong subsystem.
          <ErrorState
            what="arrays"
            error={arraysErrorObj}
            onRetry={() => refetchArrays()}
          />
        ) : arraysLoading ? (
          <p className="text-gray-500 text-sm">Loading arrays…</p>
        ) : filteredArrays.length === 0 ? (
          <p className="text-gray-500 text-sm">
            {arrays.length === 0
              ? 'No arrays found — check collectors are running and USM-Managed-Arrays is active.'
              : 'No arrays match your filters.'}
          </p>
        ) : (
          <>
            {onPremArrays.length > 0 && (
              <DeploymentSection type="on-prem" arrays={onPremArrays} onDblClick={(n) => openDrilldown(n)} />
            )}
            {cloudArrays.length > 0 && (
              <DeploymentSection type="cloud" arrays={cloudArrays} onDblClick={(n) => openDrilldown(n)} />
            )}
          </>
        )}
      </div>

      {/* Active alerts */}
      {alerts.length > 0 && (
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-gray-300">Active Alerts</h3>
            <button
              onClick={() => navigate('/alerts')}
              className="text-xs text-brand-400 hover:text-brand-300"
            >
              View all →
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-700/50 bg-gray-800/40">
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Severity</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Array</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Event</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Component</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Opened</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {alerts.slice(0, 20).map((alert, i) => (
                  <tr key={alert.id ?? i} className="hover:bg-gray-800/30">
                    <td className="px-4 py-2">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded border ${severityBg(alert.severity)}`}>
                        {alert.severity.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-gray-200 font-mono text-xs">{alert.array_name}</td>
                    <td className="px-4 py-2 text-gray-400 max-w-xs truncate">{alert.event || '—'}</td>
                    <td className="px-4 py-2 text-gray-500">{alert.component_name || alert.component_type || '—'}</td>
                    <td className="px-4 py-2 text-gray-500">{alert.opened || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Arrays table modal */}
      {showArraysTable && (
        <ArraysTableModal
          onClose={() => setShowArraysTable(false)}
          onSelectArray={(name, tab) => openDrilldown(name, tab)}
        />
      )}

      {/* Drilldown modal — rendered last (z-50) so it stacks above the table modal (z-40) */}
      {selectedArray && (
        <ArrayModal
          arrayName={selectedArray}
          initialTab={drilldownTab}
          onClose={() => setSelectedArray(null)}
        />
      )}
    </div>
  )
}
