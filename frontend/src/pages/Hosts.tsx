import { useState, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, Filter, ChevronUp, ChevronDown, X, Download, Copy, Check } from 'lucide-react'
import { arraysApi } from '@/api/arrays'
import { hostsApi } from '@/api/hosts'
import type { ArraySummary, Host } from '@/api/types'

const PAGE_SIZE = 50

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

// ── Vendor badge ──────────────────────────────────────────────────────────────

const VENDOR_COLORS: Record<string, string> = {
  pure:    'bg-orange-500/10 text-orange-400 border-orange-500/20',
  netapp:  'bg-blue-500/10 text-blue-400 border-blue-500/20',
  hpe:     'bg-green-500/10 text-green-400 border-green-500/20',
  hitachi: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
  dell:    'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
  oracle:  'bg-red-500/10 text-red-400 border-red-500/20',
}

// ── Host detail modal ─────────────────────────────────────────────────────────

function HostModal({ host, onClose }: { host: Host; onClose: () => void }) {
  function formatText() {
    const lines = [
      `Host: ${host.host_name}`,
      `Array: ${host.array_name}`,
      `Vendor: ${host.vendor}`,
      `Host Group: ${host.host_group || 'None'}`,
      `IQN: ${host.iqn || 'None'}`,
      `WWN: ${host.wwn || 'None'}`,
      `NQN: ${host.nqn || 'None'}`,
      '',
      `Volumes (${(host.volumes ?? []).length}):`,
      ...(host.volumes ?? []).map((v) => `  - ${v}`),
    ]
    return lines.join('\n')
  }

  function handleDownload() {
    const blob = new Blob([formatText()], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${host.array_name}_${host.host_name}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const [copied, setCopied] = useState(false)
  function handleCopy() {
    navigator.clipboard.writeText(formatText())
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div>
            <h2 className="text-white font-semibold text-lg truncate">{host.host_name}</h2>
            <p className="text-xs text-gray-500 mt-0.5">{host.array_name} · {host.vendor}</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleCopy} className="text-gray-400 hover:text-white p-1" title="Copy">
              {copied ? <Check size={16} className="text-green-400" /> : <Copy size={16} />}
            </button>
            <button onClick={handleDownload} className="text-gray-400 hover:text-white p-1" title="Download">
              <Download size={16} />
            </button>
            <button onClick={onClose} className="text-gray-400 hover:text-white p-1"><X size={18} /></button>
          </div>
        </div>
        <div className="p-4 space-y-4">
          {/* Volumes */}
          <div>
            <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">
              Mapped Volumes ({(host.volumes ?? []).length})
            </h4>
            {(host.volumes ?? []).length === 0 ? (
              <p className="text-gray-600 text-xs">No volumes mapped</p>
            ) : (
              (host.volumes ?? []).map((v, idx) => (
                <span key={idx} className="inline-block mr-2 mb-1 px-2 py-0.5 bg-gray-800 text-gray-300 rounded text-xs font-mono">
                  {v}
                </span>
              ))
            )}
          </div>

          {/* Connection details */}
          <div className="bg-gray-800 rounded-lg p-3">
            <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Connection Details</h4>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-gray-500">Host Group</dt><dd className="text-gray-200">{host.host_group || '—'}</dd>
              <dt className="text-gray-500">iSCSI IQN</dt><dd className="text-gray-200 font-mono text-xs truncate">{host.iqn || '—'}</dd>
              <dt className="text-gray-500">FC WWN</dt><dd className="text-gray-200 font-mono text-xs truncate">{host.wwn || '—'}</dd>
              <dt className="text-gray-500">NVMe NQN</dt><dd className="text-gray-200 font-mono text-xs truncate">{host.nqn || '—'}</dd>
              <dt className="text-gray-500">Last Updated</dt><dd className="text-gray-200">{host.last_updated || '—'}</dd>
            </dl>
          </div>
        </div>
      </div>
    </div>
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

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Hosts() {
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [arrayFilter, setArrayFilter] = useState('')
  const [vendorFilter, setVendorFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [sortBy, setSortBy] = useState('')
  const [sortDir, setSortDir] = useState('asc')
  const [selectedHost, setSelectedHost] = useState<Host | null>(null)

  useEffect(() => {
    const t = setTimeout(() => { setDebouncedSearch(search); setOffset(0) }, 300)
    return () => clearTimeout(t)
  }, [search])

  useEffect(() => { setOffset(0) }, [arrayFilter, vendorFilter])

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
      setSortDir('asc')
    }
    setOffset(0)
  }

  const { data: result, isLoading } = useQuery({
    queryKey: ['hosts', arrayFilter, debouncedSearch, vendorFilter, offset, sortBy, sortDir],
    queryFn: () => hostsApi.list({
      array_name: arrayFilter || undefined,
      search: debouncedSearch || undefined,
      vendor: vendorFilter || undefined,
      limit: PAGE_SIZE,
      offset,
      sort_by: sortBy || undefined,
      sort_dir: sortDir,
    }),
  })

  const hosts = result?.data ?? []
  const total = result?.total ?? 0

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-white">
          Hosts <span className="text-gray-500 text-sm ml-2">({total.toLocaleString()})</span>
        </h2>
        <div className="flex flex-wrap gap-2">
          <div className="relative">
            <Filter size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
            <select
              value={vendorFilter}
              onChange={(e) => setVendorFilter(e.target.value)}
              className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg pl-8 pr-3 py-1.5 focus:outline-none focus:border-brand-500 appearance-none cursor-pointer"
            >
              <option value="">All vendors</option>
              {uniqueVendors.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <select
            value={arrayFilter}
            onChange={(e) => setArrayFilter(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500 w-52 appearance-none cursor-pointer"
          >
            <option value="">All arrays</option>
            {uniqueArrays.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              placeholder="Search hosts..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg pl-8 pr-3 py-1.5 focus:outline-none focus:border-brand-500 w-48"
            />
          </div>
        </div>
      </div>

      <div className="card overflow-x-auto">
        {isLoading ? (
          <p className="text-gray-500 text-sm">Loading...</p>
        ) : hosts.length === 0 ? (
          <p className="text-gray-500 text-sm py-4 text-center">No hosts found</p>
        ) : (
          <>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-700/50 bg-gray-800/40">
                  <SortHeader label="Host" field="host_name" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                  <SortHeader label="Array" field="array_name" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                  <SortHeader label="Vendor" field="vendor" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                  <SortHeader label="Host Group" field="host_group" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">iSCSI IQN</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">FC WWN</th>
                  <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Volumes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {hosts.map((h) => (
                  <tr
                    key={`${h.array_name}/${h.host_name}`}
                    className="hover:bg-gray-800/30 cursor-pointer transition-colors"
                    onClick={() => setSelectedHost(h)}
                  >
                    <td className="px-4 py-2 font-mono text-xs text-gray-200 hover:text-brand-400">{h.host_name}</td>
                    <td className="px-4 py-2 text-xs text-gray-400">{h.array_name}</td>
                    <td className="px-4 py-2 text-xs">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase border ${
                        VENDOR_COLORS[h.vendor] ?? 'bg-gray-500/10 text-gray-400 border-gray-500/20'
                      }`}>{h.vendor}</span>
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-400">{h.host_group || '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 font-mono truncate max-w-[180px]">{h.iqn || '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 font-mono truncate max-w-[180px]">{h.wwn || '—'}</td>
                    <td className="px-4 py-2 text-right text-gray-400 text-xs">{(h.volumes ?? []).length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={total} offset={offset} limit={PAGE_SIZE} onChange={setOffset} />
          </>
        )}
      </div>

      {selectedHost && (
        <HostModal host={selectedHost} onClose={() => setSelectedHost(null)} />
      )}
    </div>
  )
}
