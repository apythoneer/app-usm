import { useState, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Filter, Search, ChevronUp, ChevronDown } from 'lucide-react'
import { arraysApi } from '@/api/arrays'
import { alertsApi } from '@/api/alerts'
import { severityBg } from '@/utils/formatters'
import type { ArraySummary, Severity } from '@/api/types'

const PAGE_SIZE = 50
const SEVERITIES: Severity[] = ['critical', 'warning', 'info']

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

// ── Pagination ────────────────────────────────────────────────────────────────

function Pagination({ total, offset, limit, onChange }: {
  total: number; offset: number; limit: number; onChange: (offset: number) => void
}) {
  const totalPages = Math.ceil(total / limit)
  const currentPage = Math.floor(offset / limit) + 1
  if (totalPages <= 1) return null

  function goTo(page: number) { onChange((page - 1) * limit) }

  return (
    <div className="flex items-center justify-between py-3 px-1 border-t border-gray-800 mt-2">
      <span className="text-xs text-gray-500">
        {offset + 1}–{Math.min(offset + limit, total)} of {total.toLocaleString()}
      </span>
      <div className="flex gap-1">
        {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
          let page: number
          if (totalPages <= 7) page = i + 1
          else if (currentPage <= 4) page = i + 1
          else if (currentPage >= totalPages - 3) page = totalPages - 6 + i
          else page = currentPage - 3 + i
          return (
            <button
              key={page}
              onClick={() => goTo(page)}
              className={`text-xs px-2.5 py-1 rounded ${
                page === currentPage
                  ? 'bg-brand-600/30 text-brand-400'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800'
              }`}
            >
              {page}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── Vendor badge ──────────────────────────────────────────────────────────────

const VENDOR_COLORS: Record<string, string> = {
  pure:    'bg-orange-500/10 text-orange-400 border-orange-500/20',
  netapp:  'bg-blue-500/10 text-blue-400 border-blue-500/20',
  hpe:     'bg-green-500/10 text-green-400 border-green-500/20',
  hitachi: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
  dell:    'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
  oracle:  'bg-red-500/10 text-red-400 border-red-500/20',
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Alerts() {
  const [severity, setSeverity] = useState<Severity | undefined>()
  const [vendorFilter, setVendorFilter] = useState('')
  const [arrayFilter, setArrayFilter] = useState('')
  const [showResolved, setShowResolved] = useState(false)
  const [offset, setOffset] = useState(0)
  const [sortBy, setSortBy] = useState('')
  const [sortDir, setSortDir] = useState('desc')

  // Reset offset on filter change
  useEffect(() => { setOffset(0) }, [severity, vendorFilter, arrayFilter, showResolved])

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

  const { data: result, isLoading } = useQuery({
    queryKey: ['alerts', severity, vendorFilter, arrayFilter, showResolved, offset, sortBy, sortDir],
    queryFn: () => alertsApi.list({
      severity,
      vendor: vendorFilter || undefined,
      array_name: arrayFilter || undefined,
      resolved: showResolved || undefined,
      limit: PAGE_SIZE,
      offset,
      sort_by: sortBy || undefined,
      sort_dir: sortDir,
    }),
  })

  const alerts = result?.data ?? []
  const total = result?.total ?? 0

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-white">
          Alerts <span className="text-gray-500 text-sm ml-2">({total.toLocaleString()})</span>
        </h2>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 items-center">
        {/* Severity pills */}
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

        {/* Resolved toggle */}
        <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer ml-auto">
          <input
            type="checkbox"
            checked={showResolved}
            onChange={(e) => setShowResolved(e.target.checked)}
            className="accent-brand-500"
          />
          Show resolved
        </label>
      </div>

      {/* Table */}
      <div className="card">
        {isLoading ? (
          <p className="text-gray-500 text-sm">Loading...</p>
        ) : alerts.length === 0 ? (
          <p className="text-gray-500 text-sm py-4 text-center">No alerts found</p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-700/50 bg-gray-800/40">
                    <SortHeader label="Severity" field="severity" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Array" field="array_name" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Vendor" field="vendor" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Event" field="event" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Component" field="component_name" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <SortHeader label="Opened" field="opened" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                    <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Teams</th>
                    <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">SNOW</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800/50">
                  {alerts.map((alert) => (
                    <tr key={alert.id} className="hover:bg-gray-800/30">
                      <td className="px-4 py-2">
                        <span className={`text-xs font-medium px-2 py-0.5 rounded border ${severityBg(alert.severity)}`}>
                          {alert.severity}
                        </span>
                      </td>
                      <td className="px-4 py-2 font-mono text-xs text-gray-200">{alert.array_name}</td>
                      <td className="px-4 py-2 text-xs">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase border ${
                          VENDOR_COLORS[alert.vendor] ?? 'bg-gray-500/10 text-gray-400 border-gray-500/20'
                        }`}>{alert.vendor}</span>
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-300 max-w-xs truncate">{alert.event || '—'}</td>
                      <td className="px-4 py-2 text-xs text-gray-400">{alert.component_name || '—'}</td>
                      <td className="px-4 py-2 text-xs text-gray-500">{alert.opened || '—'}</td>
                      <td className="px-4 py-2 text-xs">
                        {alert.teams_notified
                          ? <span className="text-green-400" title={alert.teams_notified}>&#10003;</span>
                          : <span className="text-gray-600">—</span>}
                      </td>
                      <td className="px-4 py-2 text-xs font-mono text-gray-400">
                        {alert.snow_ticket || <span className="text-gray-600">—</span>}
                      </td>
                    </tr>
                  ))}
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
