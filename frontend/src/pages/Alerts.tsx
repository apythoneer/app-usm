import { useState, useEffect, useMemo, Fragment } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Filter, ChevronUp, ChevronDown, ChevronRight, Repeat } from 'lucide-react'
import ErrorState from '@/components/common/ErrorState'
import Pagination from '@/components/common/Pagination'
import VendorBadge from '@/components/common/VendorBadge'
import { arraysApi } from '@/api/arrays'
import { alertsApi } from '@/api/alerts'
import { severityBg } from '@/utils/formatters'
import type { Alert, ArraySummary, Severity } from '@/api/types'

const PAGE_SIZE = 50
const SEVERITIES: Severity[] = ['critical', 'warning', 'info']
type StatusFilter = 'active' | 'resolved' | 'all'
const STATUSES: { key: StatusFilter; label: string }[] = [
  { key: 'active', label: 'Active' },
  { key: 'resolved', label: 'Resolved' },
  { key: 'all', label: 'All' },
]

// Compact timestamp: "Jun 25, 21:35" with the raw value on hover. Storage APIs
// hand back a mix of ISO ("...Z"), space-separated, and already-local strings;
// anything unparseable falls back to the raw text so nothing renders blank.
function fmtTs(v?: string | null): string {
  if (!v) return '—'
  const d = new Date(v.includes('T') || v.includes('Z') ? v : v.replace(' ', 'T'))
  if (isNaN(d.getTime())) return v
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

// ── Sort header ───────────────────────────────────────────────────────────────

function SortHeader({ label, field, sortBy, sortDir, onSort, align = 'left' }: {
  label: string; field: string; sortBy: string; sortDir: string
  onSort: (field: string) => void; align?: 'left' | 'right'
}) {
  const active = sortBy === field
  return (
    <th
      className={`px-4 py-2 text-xs text-gray-500 uppercase tracking-wide cursor-pointer hover:text-gray-300 select-none ${
        align === 'right' ? 'text-right' : 'text-left'
      }`}
      onClick={() => onSort(field)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active ? (
          sortDir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />
        ) : (
          <span className="w-3" />
        )}
      </span>
    </th>
  )
}

// ── Expanded detail row ─────────────────────────────────────────────────────────

function DetailField({ label, value, mono }: { label: string; value?: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`text-xs text-gray-200 break-words ${mono ? 'font-mono' : ''}`}>
        {value === undefined || value === null || value === '' ? <span className="text-gray-600">—</span> : value}
      </div>
    </div>
  )
}

function AlertDetail({ alert, colSpan }: { alert: Alert; colSpan: number }) {
  const occ = alert.event_occurrences ?? alert.occurrence_count ?? 1
  return (
    <tr className="bg-gray-900/60">
      <td colSpan={colSpan} className="px-6 py-4 border-b border-gray-800">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-3">
          <DetailField label="Event" value={alert.event} />
          <DetailField label="Component" value={
            [alert.component_type, alert.component_name].filter(Boolean).join(': ') || undefined
          } />
          <DetailField label="Message ID" value={alert.message_id} mono />
          <DetailField label="Severity" value={
            <span className={`text-xs font-medium px-2 py-0.5 rounded border ${severityBg(alert.severity)}`}>{alert.severity}</span>
          } />

          <DetailField label="Reported by array" value={fmtTs(alert.opened)} />
          <DetailField label="Seen by platform (crawl)" value={fmtTs(alert.collected_at)} />
          <DetailField label="First seen" value={fmtTs(alert.first_seen)} />
          <DetailField label="Last seen" value={fmtTs(alert.last_seen)} />

          <DetailField label="Occurrences (this event / array)" value={
            <span className="inline-flex items-center gap-1">
              <Repeat size={11} className="text-amber-400" /> {occ}
              {alert.occurrence_count && alert.occurrence_count > 1
                ? <span className="text-gray-500">({alert.occurrence_count} reopens on this alert)</span> : null}
            </span>
          } />
          <DetailField label="Expected" value={alert.expected} />
          <DetailField label="Actual" value={alert.actual} />
          <DetailField label="Status" value={
            alert.resolved
              ? <span className="text-green-400">Resolved{alert.closed ? ` · ${fmtTs(alert.closed)}` : ''}</span>
              : <span className="text-amber-400">Active</span>
          } />

          <DetailField label="Datadog" value={
            alert.datadog_notified
              ? (alert.datadog_event_url
                  ? <a href={alert.datadog_event_url} target="_blank" rel="noreferrer"
                       className="text-green-400 hover:underline" title={`Event ${alert.datadog_event_id || ''}`}>Paged ✓ · view event ↗</a>
                  : <span className="text-green-400" title={alert.datadog_event_id || alert.datadog_notified}>Paged ✓</span>)
              : <span className="text-gray-500">not paged</span>
          } />
          <DetailField label="Teams" value={
            alert.teams_notified
              ? <span className="text-green-400" title={alert.teams_notified}>Sent ✓</span>
              : <span className="text-gray-500">—</span>
          } />
          <DetailField label="Vendor" value={<VendorBadge vendor={alert.vendor} />} />
          <DetailField label="Array" value={alert.array_name} mono />
        </div>
      </td>
    </tr>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Alerts() {
  const [severity, setSeverity] = useState<Severity | undefined>()
  const [vendorFilter, setVendorFilter] = useState('')
  const [arrayFilter, setArrayFilter] = useState('')
  const [status, setStatus] = useState<StatusFilter>('active')
  const [offset, setOffset] = useState(0)
  const [sortBy, setSortBy] = useState('')
  const [sortDir, setSortDir] = useState('desc')
  const [expanded, setExpanded] = useState<number | null>(null)

  // Reset offset on filter change
  useEffect(() => { setOffset(0); setExpanded(null) }, [severity, vendorFilter, arrayFilter, status])

  // Array list for filters
  const { data: arrays = [] } = useQuery<ArraySummary[]>({
    queryKey: ['arrays'],
    queryFn: () => arraysApi.list(),
    staleTime: 60_000,
  })

  const uniqueArrays = useMemo(() => [...new Set(arrays.map((a) => a.array_name))].sort(), [arrays])
  const uniqueVendors = useMemo(() => [...new Set(arrays.map((a) => a.vendor))].sort(), [arrays])

  function handleSort(field: string) {
    if (sortBy === field) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(field)
      setSortDir('desc')
    }
    setOffset(0)
  }

  const { data: result, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['alerts', severity, vendorFilter, arrayFilter, status, offset, sortBy, sortDir],
    queryFn: () => alertsApi.list({
      severity,
      vendor: vendorFilter || undefined,
      array_name: arrayFilter || undefined,
      // active -> resolved:false, resolved -> resolved:true, all -> omit the filter
      // (backend treats absent `resolved` as "both").
      resolved: status === 'all' ? undefined : status === 'resolved',
      limit: PAGE_SIZE,
      offset,
      sort_by: sortBy || undefined,
      sort_dir: sortDir,
    }),
  })

  const alerts = result?.data ?? []
  const total = result?.total ?? 0
  const COLS = 10  // expand + severity + array + vendor + event + component + occ + reported + seen + datadog

  function toggle(id?: number) {
    if (id == null) return
    setExpanded((cur) => (cur === id ? null : id))
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-white">
          Alerts <span className="text-gray-500 text-sm ml-2">({total.toLocaleString()})</span>
        </h2>
        <span className="text-xs text-gray-500">Double-click a row (or ▸) to expand</span>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 items-center">
        <button
          onClick={() => setSeverity(undefined)}
          className={`text-xs px-3 py-1.5 rounded border ${!severity ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'text-gray-400 border-gray-700 hover:border-gray-500'}`}
        >All</button>
        {SEVERITIES.map((s) => (
          <button
            key={s}
            onClick={() => setSeverity(s === severity ? undefined : s)}
            className={`text-xs px-3 py-1.5 rounded border ${severity === s ? severityBg(s) : 'text-gray-400 border-gray-700 hover:border-gray-500'}`}
          >{s}</button>
        ))}

        <span className="text-gray-700 mx-1">|</span>

        {/* Vendor filter */}
        <div className="relative">
          <Filter size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500" />
          <select
            value={vendorFilter}
            onChange={(e) => setVendorFilter(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg pl-7 pr-3 py-1.5 focus:outline-none focus:border-brand-500 appearance-none cursor-pointer"
          >
            <option value="">All vendors</option>
            {uniqueVendors.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>

        {/* Array filter */}
        <select
          value={arrayFilter}
          onChange={(e) => setArrayFilter(e.target.value)}
          className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500 w-48 appearance-none cursor-pointer"
        >
          <option value="">All arrays</option>
          {uniqueArrays.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>

        {/* Status filter: Active / Resolved / All */}
        <div className="ml-auto inline-flex rounded-lg border border-gray-700 overflow-hidden">
          {STATUSES.map((s) => (
            <button
              key={s.key}
              onClick={() => setStatus(s.key)}
              className={`text-xs px-3 py-1.5 border-l first:border-l-0 border-gray-700 ${
                status === s.key
                  ? 'bg-brand-600/20 text-brand-400'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/50'
              }`}
            >{s.label}</button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="card">
        {isError ? (
          <ErrorState what="alerts" error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <p className="text-gray-500 text-sm">Loading...</p>
        ) : alerts.length === 0 ? (
          <p className="text-gray-500 text-sm py-4 text-center">No alerts found</p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-700/50 bg-gray-800/40">
                    <th className="w-8" />
                    <SortHeader label="Severity" field="severity" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Array" field="array_name" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Vendor" field="vendor" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Event" field="event" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Component" field="component_name" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Occurrences" field="event_occurrences" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} align="right" />
                    <SortHeader label="Reported (array)" field="opened" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Seen (platform)" field="last_seen" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Datadog</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800/50">
                  {alerts.map((alert) => {
                    const occ = alert.event_occurrences ?? alert.occurrence_count ?? 1
                    const isOpen = expanded === alert.id
                    return (
                      <Fragment key={alert.id}>
                        <tr
                          className="hover:bg-gray-800/30 cursor-pointer"
                          onDoubleClick={() => toggle(alert.id)}
                        >
                          <td className="px-2 py-2 text-gray-500" onClick={(e) => { e.stopPropagation(); toggle(alert.id) }}>
                            {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          </td>
                          <td className="px-4 py-2">
                            <span className={`text-xs font-medium px-2 py-0.5 rounded border ${severityBg(alert.severity)}`}>
                              {alert.severity}
                            </span>
                          </td>
                          <td className="px-4 py-2 font-mono text-xs text-gray-200">{alert.array_name}</td>
                          <td className="px-4 py-2 text-xs"><VendorBadge vendor={alert.vendor} /></td>
                          <td className="px-4 py-2 text-xs text-gray-300 max-w-xs truncate" title={alert.event || ''}>{alert.event || '—'}</td>
                          <td className="px-4 py-2 text-xs text-gray-400">{alert.component_name || '—'}</td>
                          <td className="px-4 py-2 text-xs text-right">
                            {occ > 1
                              ? <span className="inline-flex items-center gap-1 text-amber-400 font-medium" title="Times this event occurred on this array">
                                  <Repeat size={11} />{occ}×
                                </span>
                              : <span className="text-gray-500">1×</span>}
                          </td>
                          <td className="px-4 py-2 text-xs text-gray-400 whitespace-nowrap" title={alert.opened || ''}>{fmtTs(alert.opened)}</td>
                          <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap" title={alert.collected_at || alert.last_seen || ''}>{fmtTs(alert.last_seen || alert.collected_at)}</td>
                          <td className="px-4 py-2 text-xs">
                            {alert.datadog_notified
                              ? <span className="text-green-400" title={`Paged to Datadog · ${alert.datadog_notified}`}>&#10003;</span>
                              : <span className="text-gray-600">—</span>}
                          </td>
                        </tr>
                        {isOpen && <AlertDetail alert={alert} colSpan={COLS} />}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <Pagination total={total} offset={offset} limit={PAGE_SIZE} onChange={setOffset} />
          </>
        )}
      </div>
    </div>
  )
}
