import { useState, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X, Copy, Download, ChevronLeft, ChevronRight, Search, Filter } from 'lucide-react'
import { hostsApi } from '@/api/hosts'
import { arraysApi } from '@/api/arrays'
import type { Host, ArraySummary } from '@/api/types'

const PAGE_SIZE = 50

// ── Host drilldown modal ──────────────────────────────────────────────────────

function HostModal({ host, onClose }: { host: Host; onClose: () => void }) {
  function formatText() {
    return [
      `Host:          ${host.host_name}`,
      `Array:         ${host.array_name}`,
      `Vendor:        ${host.vendor}`,
      `Host Group:    ${host.host_group || '—'}`,
      ``,
      `iSCSI IQN:     ${host.iqn || '—'}`,
      `FC WWN:        ${host.wwn || '—'}`,
      `NVMe NQN:      ${host.nqn || '—'}`,
      ``,
      `Connected Volumes (${(host.volumes ?? []).length}):`,
      ...(host.volumes ?? []).map((v) => `  - ${v}`),
    ].join('\n')
  }

  function handleCopy() {
    navigator.clipboard.writeText(formatText())
  }

  function handleDownload() {
    const blob = new Blob([formatText()], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${host.host_name.replace(/[^a-z0-9]/gi, '_')}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-xl mx-4 max-h-[90vh] overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div>
            <h2 className="text-white font-semibold font-mono">{host.host_name}</h2>
            <p className="text-xs text-gray-500 mt-0.5">{host.array_name} · {host.vendor}</p>
          </div>
          <div className="flex items-center gap-2 ml-4">
            <button onClick={handleCopy} className="text-gray-400 hover:text-white p-1.5 rounded hover:bg-gray-700" title="Copy to clipboard">
              <Copy size={16} />
            </button>
            <button onClick={handleDownload} className="text-gray-400 hover:text-white p-1.5 rounded hover:bg-gray-700" title="Download .txt">
              <Download size={16} />
            </button>
            <button onClick={onClose} className="text-gray-400 hover:text-white p-1.5 rounded hover:bg-gray-700">
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="p-4 space-y-4">
          {/* Connectivity map */}
          <div className="bg-gray-800 rounded-lg p-3">
            <p className="text-xs text-gray-500 uppercase tracking-wide mb-3">Connectivity Map</p>
            <div className="font-mono text-xs space-y-0.5">
              <div className="flex items-center gap-1.5 text-brand-400">
                <span>◆</span>
                <span className="font-semibold">{host.array_name}</span>
                <span className="text-gray-600 text-[10px] ml-1">array</span>
              </div>
              <div className="flex items-start gap-0">
                <span className="text-gray-600 w-4 flex-shrink-0">└─</span>
                <div className="flex items-center gap-1.5 text-green-400">
                  <span>▸</span>
                  <span>{host.host_name}</span>
                  <span className="text-gray-600 text-[10px]">host</span>
                </div>
              </div>
              {(host.volumes ?? []).length === 0 ? (
                <div className="flex items-center gap-0">
                  <span className="text-gray-700 w-8 flex-shrink-0">   └─</span>
                  <span className="text-gray-600 italic">no connected volumes</span>
                </div>
              ) : (
                (host.volumes ?? []).map((v, idx) => {
                  const isLast = idx === (host.volumes ?? []).length - 1
                  return (
                    <div key={v} className="flex items-center gap-0">
                      <span className="text-gray-700 w-8 flex-shrink-0">   {isLast ? '└─' : '├─'}</span>
                      <span className="text-cyan-400">{v}</span>
                      <span className="text-gray-600 text-[10px] ml-1.5">volume</span>
                    </div>
                  )
                })
              )}
            </div>
          </div>

          {/* Identity */}
          <div className="bg-gray-800 rounded-lg p-3">
            <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">Identity</p>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-gray-500">Host Group</dt>
              <dd className="text-white">{host.host_group || '—'}</dd>
              <dt className="text-gray-500">iSCSI IQN</dt>
              <dd className="text-white font-mono text-xs break-all">{host.iqn || '—'}</dd>
              <dt className="text-gray-500">FC WWN</dt>
              <dd className="text-white font-mono text-xs break-all">{host.wwn || '—'}</dd>
              <dt className="text-gray-500">NVMe NQN</dt>
              <dd className="text-white font-mono text-xs break-all">{host.nqn || '—'}</dd>
            </dl>
          </div>

          {/* Connected volumes */}
          <div className="bg-gray-800 rounded-lg p-3">
            <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">
              Connected Volumes ({(host.volumes ?? []).length})
            </p>
            {(host.volumes ?? []).length === 0 ? (
              <p className="text-sm text-gray-500">No connected volumes</p>
            ) : (
              <ul className="space-y-1 max-h-60 overflow-y-auto">
                {(host.volumes ?? []).map((v) => (
                  <li key={v} className="text-sm text-gray-300 font-mono py-0.5">{v}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Pagination controls ───────────────────────────────────────────────────────

function Pagination({ total, offset, limit, onChange }: {
  total: number; offset: number; limit: number; onChange: (offset: number) => void
}) {
  const totalPages = Math.ceil(total / limit)
  const currentPage = Math.floor(offset / limit) + 1
  if (totalPages <= 1) return null

  return (
    <div className="flex items-center justify-between pt-3 border-t border-gray-700/50">
      <p className="text-xs text-gray-500">
        Showing {offset + 1}–{Math.min(offset + limit, total)} of {total.toLocaleString()}
      </p>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onChange(Math.max(0, offset - limit))}
          disabled={offset === 0}
          className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <ChevronLeft size={16} />
        </button>
        {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
          let page: number
          if (totalPages <= 7) {
            page = i + 1
          } else if (currentPage <= 4) {
            page = i + 1
          } else if (currentPage >= totalPages - 3) {
            page = totalPages - 6 + i
          } else {
            page = currentPage - 3 + i
          }
          return (
            <button
              key={page}
              onClick={() => onChange((page - 1) * limit)}
              className={`min-w-[28px] h-7 rounded text-xs font-medium ${
                page === currentPage
                  ? 'bg-brand-600 text-white'
                  : 'text-gray-400 hover:bg-gray-700 hover:text-white'
              }`}
            >
              {page}
            </button>
          )
        })}
        <button
          onClick={() => onChange(Math.min((totalPages - 1) * limit, offset + limit))}
          disabled={offset + limit >= total}
          className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  )
}

// ── Hosts page ────────────────────────────────────────────────────────────────

export default function Hosts() {
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [arrayFilter, setArrayFilter] = useState('')
  const [vendorFilter, setVendorFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [selectedHost, setSelectedHost] = useState<Host | null>(null)

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => { setDebouncedSearch(search); setOffset(0) }, 300)
    return () => clearTimeout(t)
  }, [search])

  useEffect(() => { setOffset(0) }, [arrayFilter, vendorFilter])

  // Fetch array list for dropdown
  const { data: arrays = [] } = useQuery<ArraySummary[]>({
    queryKey: ['arrays'],
    queryFn: () => arraysApi.list(),
    staleTime: 60_000,
  })

  const uniqueArrays = useMemo(() =>
    [...new Set(arrays.map((a) => a.array_name))].sort(),
    [arrays]
  )
  const uniqueVendors = useMemo(() =>
    [...new Set(arrays.map((a) => a.vendor))].sort(),
    [arrays]
  )

  const { data: result, isLoading } = useQuery({
    queryKey: ['hosts', arrayFilter, debouncedSearch, vendorFilter, offset],
    queryFn: () => hostsApi.list({
      array_name: arrayFilter || undefined,
      search: debouncedSearch || undefined,
      vendor: vendorFilter || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
  })

  const hosts = result?.data ?? []
  const total = result?.total ?? 0

  return (
    <div className="space-y-4">
      {/* Header + filters */}
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
              {uniqueVendors.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
          <select
            value={arrayFilter}
            onChange={(e) => setArrayFilter(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500 w-52 appearance-none cursor-pointer"
          >
            <option value="">All arrays</option>
            {uniqueArrays.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
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
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Host</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Array</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Vendor</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Host Group</th>
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
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${
                        h.vendor === 'pure' ? 'bg-orange-500/10 text-orange-400 border border-orange-500/20' :
                        h.vendor === 'netapp' ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20' :
                        'bg-gray-500/10 text-gray-400 border border-gray-500/20'
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
