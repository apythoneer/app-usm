import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Server, AlertTriangle, Database, Activity, Zap, Clock,
  BarChart2, Users, ChevronDown, ChevronRight, X, HardDrive
} from 'lucide-react'
import { arraysApi } from '@/api/arrays'
import { alertsApi } from '@/api/alerts'
import { volumesApi } from '@/api/volumes'
import { hostsApi } from '@/api/hosts'
import type { ArraySummary, ArrayMetrics, Alert, FleetStats } from '@/api/types'
import {
  formatBytes, formatIOPS, formatLatency, formatPct,
  formatReduction, severityBg, usedPctColor
} from '@/utils/formatters'

// ── Fleet stat card ───────────────────────────────────────────────────────────

function StatCard({ icon: Icon, label, value, sub, color = 'text-brand-400' }: {
  icon: React.ElementType; label: string; value: string; sub?: string; color?: string
}) {
  return (
    <div className="card flex items-start gap-3 p-4">
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

function ArrayModal({ arrayName, onClose }: { arrayName: string; onClose: () => void }) {
  const [tab, setTab] = useState<DrilldownTab>('overview')

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
          <button onClick={onClose} className="text-gray-400 hover:text-white ml-4"><X size={20} /></button>
        </div>

        {/* Tab bar */}
        <div className="flex border-b border-gray-700 px-4">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors ${
                tab === t.key
                  ? 'border-brand-500 text-white'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              {t.label}
              {t.count != null && <span className="ml-1.5 text-gray-600">({t.count})</span>}
            </button>
          ))}
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-gray-400">Loading…</div>
        ) : !data ? (
          <div className="p-8 text-center text-gray-500">No data available</div>
        ) : tab === 'overview' ? (
          <div className="p-4 grid grid-cols-2 gap-4">
            {/* Identity */}
            <div className="col-span-2 bg-gray-800 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-2 uppercase tracking-wide">Identity</p>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
                <dt className="text-gray-500">Vendor</dt><dd className="text-white capitalize">{data.vendor}</dd>
                <dt className="text-gray-500">Firmware</dt><dd className="text-white">{data.firmware_version || '—'}</dd>
                <dt className="text-gray-500">Model</dt><dd className="text-white">{data.model || '—'}</dd>
                <dt className="text-gray-500">Uptime</dt><dd className="text-white">{data.uptime_str || '—'}</dd>
                <dt className="text-gray-500">Array status</dt>
                <dd className={`font-medium ${data.array_status === 'ok' || data.array_status === 'healthy' ? 'text-green-400' : 'text-red-400'}`}>
                  {data.array_status || '—'}
                </dd>
                <dt className="text-gray-500">Controller</dt>
                <dd className={`font-medium ${data.controller_status === 'ready' || data.controller_status === 'healthy' ? 'text-green-400' : 'text-yellow-400'}`}>
                  {data.controller_status || '—'}
                </dd>
              </dl>
            </div>

            {/* Performance */}
            <div className="bg-gray-800 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-2 uppercase tracking-wide">Performance</p>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
                <dt className="text-gray-500">Read IOPS</dt><dd className="text-white">{formatIOPS(data.read_iops)}</dd>
                <dt className="text-gray-500">Write IOPS</dt><dd className="text-white">{formatIOPS(data.write_iops)}</dd>
                <dt className="text-gray-500">Read latency</dt><dd className="text-white">{formatLatency(data.read_latency_us)}</dd>
                <dt className="text-gray-500">Write latency</dt><dd className="text-white">{formatLatency(data.write_latency_us)}</dd>
                <dt className="text-gray-500">Read BW</dt><dd className="text-white">{formatBytes(data.read_bandwidth_bytes)}/s</dd>
                <dt className="text-gray-500">Write BW</dt><dd className="text-white">{formatBytes(data.write_bandwidth_bytes)}/s</dd>
              </dl>
            </div>

            {/* Capacity */}
            <div className="bg-gray-800 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-2 uppercase tracking-wide">Capacity</p>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
                <dt className="text-gray-500">Total</dt><dd className="text-white">{formatBytes(data.capacity_total_bytes)}</dd>
                <dt className="text-gray-500">Used</dt><dd className="text-white">{formatBytes(data.capacity_used_bytes)}</dd>
                <dt className="text-gray-500">Utilization</dt><dd className="text-white">{formatPct(data.capacity_used_pct)}</dd>
                <dt className="text-gray-500">Data reduction</dt><dd className="text-white">{formatReduction(data.data_reduction)}</dd>
                <dt className="text-gray-500">Total reduction</dt><dd className="text-white">{formatReduction(data.total_reduction)}</dd>
                <dt className="text-gray-500">Snapshots</dt><dd className="text-white">{formatBytes(data.snapshot_space_bytes)}</dd>
              </dl>
              {data.capacity_used_pct != null && (
                <div className="mt-3 h-2 rounded-full bg-gray-700 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${usedPctColor(data.capacity_used_pct)}`}
                    style={{ width: `${Math.min(data.capacity_used_pct, 100)}%` }}
                  />
                </div>
              )}
            </div>
          </div>
        ) : tab === 'volumes' ? (
          <div className="p-4">
            {volumes.length === 0 ? (
              <p className="text-gray-500 text-sm text-center py-4">No volumes found</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-700/50 bg-gray-800/40">
                      <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase">Volume</th>
                      <th className="px-3 py-2 text-right text-xs text-gray-500 uppercase">Size</th>
                      <th className="px-3 py-2 text-right text-xs text-gray-500 uppercase">Used</th>
                      <th className="px-3 py-2 text-right text-xs text-gray-500 uppercase">Reduction</th>
                      <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase">Hosts</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-800/50">
                    {volumes.map((v) => (
                      <tr key={v.volume_name} className="hover:bg-gray-800/30">
                        <td className="px-3 py-1.5 font-mono text-xs text-gray-200 truncate max-w-[200px]">{v.volume_name}</td>
                        <td className="px-3 py-1.5 text-right text-gray-300 text-xs">{formatBytes(v.size_bytes)}</td>
                        <td className="px-3 py-1.5 text-right text-gray-300 text-xs">{formatBytes(v.used_bytes)}</td>
                        <td className="px-3 py-1.5 text-right text-gray-300 text-xs">{formatReduction(v.data_reduction)}</td>
                        <td className="px-3 py-1.5 text-xs text-gray-500 truncate max-w-[150px]">
                          {(v.hosts ?? []).slice(0, 2).join(', ') || '—'}
                          {(v.hosts ?? []).length > 2 && ` +${(v.hosts ?? []).length - 2}`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {(volResult?.total ?? 0) > 200 && (
                  <p className="text-xs text-gray-600 text-center mt-2">Showing first 200 of {volResult?.total} — view all on Volumes page</p>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="p-4">
            {hosts.length === 0 ? (
              <p className="text-gray-500 text-sm text-center py-4">No hosts found</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-700/50 bg-gray-800/40">
                      <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase">Host</th>
                      <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase">Host Group</th>
                      <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase">IQN / WWN</th>
                      <th className="px-3 py-2 text-right text-xs text-gray-500 uppercase">Volumes</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-800/50">
                    {hosts.map((h) => (
                      <tr key={h.host_name} className="hover:bg-gray-800/30">
                        <td className="px-3 py-1.5 font-mono text-xs text-gray-200 truncate max-w-[200px]">{h.host_name}</td>
                        <td className="px-3 py-1.5 text-xs text-gray-400">{h.host_group || '—'}</td>
                        <td className="px-3 py-1.5 text-xs text-gray-500 font-mono truncate max-w-[180px]">
                          {h.iqn || h.wwn || '—'}
                        </td>
                        <td className="px-3 py-1.5 text-right text-gray-400 text-xs">{(h.volumes ?? []).length}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {(hostResult?.total ?? 0) > 200 && (
                  <p className="text-xs text-gray-600 text-center mt-2">Showing first 200 of {hostResult?.total} — view all on Hosts page</p>
                )}
              </div>
            )}
          </div>
        )}
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
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusOk ? 'bg-green-400' : 'bg-red-400'}`} />
          <span className="truncate max-w-xs font-mono text-xs">{array.array_name}</span>
        </div>
      </td>
      <td className="px-4 py-2.5 text-sm text-gray-400">{array.model || '—'}</td>
      <td className="px-4 py-2.5 text-sm">
        <span className={`capitalize text-xs px-2 py-0.5 rounded-full border ${statusOk
          ? 'bg-green-500/10 text-green-400 border-green-500/20'
          : 'bg-red-500/10 text-red-400 border-red-500/20'
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

// ── Group section ─────────────────────────────────────────────────────────────

const GROUP_ICONS: Record<string, string> = {
  aws: '☁️', azure: '🔷', gcp: '🟡', 'on-prem': '🏢', 'on-premises': '🏢',
}

function GroupSection({ groupName, arrays, onDblClick }: {
  groupName: string; arrays: ArraySummary[]; onDblClick: (name: string) => void
}) {
  const [open, setOpen] = useState(true)
  const icon = GROUP_ICONS[groupName.toLowerCase()] ?? '📦'
  const displayName = groupName === 'On-Premises' ? 'On-Premises' : groupName.toUpperCase()

  return (
    <div className="mb-4">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-gray-800/60 hover:bg-gray-800 rounded-lg text-left transition-colors border border-gray-700/50"
      >
        {open
          ? <ChevronDown size={15} className="text-gray-400 flex-shrink-0" />
          : <ChevronRight size={15} className="text-gray-400 flex-shrink-0" />}
        <span>{icon}</span>
        <span className="text-sm font-medium text-gray-300">{displayName}</span>
        <span className="text-xs text-gray-500 ml-1">({arrays.length})</span>
      </button>

      {open && (
        <div className="mt-1 border border-gray-700/50 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-700/50 bg-gray-800/40">
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Array</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Version</th>
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
      )}
    </div>
  )
}

// ── Dashboard page ────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [selectedArray, setSelectedArray] = useState<string | null>(null)

  const { data: arrays = [], isLoading: arraysLoading } = useQuery<ArraySummary[]>({
    queryKey: ['arrays'],
    queryFn: () => arraysApi.list(),
    refetchInterval: 60_000,
  })

  const { data: fleet } = useQuery<FleetStats>({
    queryKey: ['fleet-stats'],
    queryFn: () => arraysApi.fleetStats(),
    refetchInterval: 60_000,
  })

  const { data: alerts = [] } = useQuery<Alert[]>({
    queryKey: ['alerts', 'active'],
    queryFn: () => alertsApi.list({ resolved: false }),
    refetchInterval: 30_000,
  })

  // Group arrays by cloud/site label
  const groups = arrays.reduce<Record<string, ArraySummary[]>>((acc, arr) => {
    const key = arr.group ?? 'On-Premises'
    ;(acc[key] = acc[key] || []).push(arr)
    return acc
  }, {})

  const criticalCount = alerts.filter((a) => a.severity === 'critical').length
  const warningCount  = alerts.filter((a) => a.severity === 'warning').length

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-white">Dashboard</h2>
        <p className="text-gray-400 mt-0.5 text-sm">Storage Intelligence Platform — fleet overview</p>
      </div>

      {/* Fleet stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
        <StatCard icon={Server}       label="Arrays"         value={String(fleet?.total_arrays ?? arrays.length)} />
        <StatCard icon={Database}     label="Total Capacity"
          value={fleet?.total_capacity_tb != null ? `${fleet.total_capacity_tb.toFixed(1)} TB` : '—'} />
        <StatCard icon={HardDrive}    label="Used"
          value={fleet?.total_used_tb != null ? `${fleet.total_used_tb.toFixed(1)} TB` : '—'}
          sub={fleet?.avg_utilization_pct != null ? `${fleet.avg_utilization_pct.toFixed(1)}% avg` : undefined} />
        <StatCard icon={BarChart2}    label="Data Reduction"
          value={fleet?.avg_data_reduction != null ? formatReduction(fleet.avg_data_reduction) : '—'} />
        <StatCard icon={Zap}          label="Total IOPS"
          value={fleet?.total_iops != null ? formatIOPS(fleet.total_iops) : '—'} />
        <StatCard icon={Clock}        label="Avg Latency (R)"
          value={fleet?.avg_read_latency_us != null ? formatLatency(fleet.avg_read_latency_us) : '—'} />
        <StatCard icon={Users}        label="Hosts / Volumes"
          value={fleet?.total_hosts != null ? String(fleet.total_hosts) : '—'}
          sub={fleet?.total_volumes != null ? `${fleet.total_volumes} volumes` : undefined} />
        <StatCard icon={AlertTriangle} label="Active Alerts"
          value={String(fleet?.active_alerts ?? alerts.length)}
          sub={criticalCount > 0 ? `${criticalCount} critical` : warningCount > 0 ? `${warningCount} warning` : 'all clear'}
          color={criticalCount > 0 ? 'text-red-400' : warningCount > 0 ? 'text-yellow-400' : 'text-green-400'} />
      </div>

      {/* Arrays by cloud group */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-300">Storage Arrays</h3>
          <span className="text-xs text-gray-500">Double-click a row for details</span>
        </div>

        {arraysLoading ? (
          <p className="text-gray-500 text-sm">Loading arrays…</p>
        ) : arrays.length === 0 ? (
          <p className="text-gray-500 text-sm">
            No arrays found — check collectors are running and USM-Managed-Arrays is active.
          </p>
        ) : (
          Object.entries(groups)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([group, groupArrays]) => (
              <GroupSection
                key={group}
                groupName={group}
                arrays={groupArrays}
                onDblClick={setSelectedArray}
              />
            ))
        )}
      </div>

      {/* Active alerts */}
      {alerts.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-gray-300 mb-4">Active Alerts</h3>
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

      {/* Drilldown modal */}
      {selectedArray && (
        <ArrayModal arrayName={selectedArray} onClose={() => setSelectedArray(null)} />
      )}
    </div>
  )
}
