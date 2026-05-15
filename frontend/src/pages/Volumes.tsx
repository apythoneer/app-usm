import { useState, useRef, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Search, Filter, ChevronUp, ChevronDown, X, Download, Copy, Check } from 'lucide-react'
import { arraysApi } from '@/api/arrays'
import { volumesApi } from '@/api/volumes'
import type { ArraySummary, Volume } from '@/api/types'
import { formatBytes, formatReduction } from '@/utils/formatters'

const PAGE_SIZE = 50

// ── Sort header component ─────────────────────────────────────────────────────

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

// ── Volume detail modal ───────────────────────────────────────────────────────

function VolumeModal({ volume, onClose }: { volume: Volume; onClose: () => void }) {
  function formatText() {
    const lines = [
      `Volume: ${volume.volume_name}`,
      `Array: ${volume.array_name}`,
      `Vendor: ${volume.vendor}`,
      `Size: ${formatBytes(volume.size_bytes)}`,
      `Used: ${formatBytes(volume.used_bytes)}`,
      `Data Reduction: ${formatReduction(volume.data_reduction)}`,
      `Serial: ${volume.serial || '—'}`,
      `Created: ${volume.created || '—'}`,
      '',
      `Hosts: ${(volume.hosts ?? []).join(', ') || 'None'}`,
      `Host Groups: ${(volume.host_groups ?? []).join(', ') || 'None'}`,
      `Protection Groups: ${(volume.protection_groups ?? []).join(', ') || 'None'}`,
    ]
    if (volume.notes) lines.push('', `Notes: ${volume.notes}`)
    return lines.join('\n')
  }

  function handleDownload() {
    const blob = new Blob([formatText()], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${volume.array_name}_${volume.volume_name.replace(/[/\\:]/g, '_')}.txt`
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
            <h2 className="text-white font-semibold text-lg truncate">{volume.volume_name}</h2>
            <p className="text-xs text-gray-500 mt-0.5">{volume.array_name} · {volume.vendor}</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleCopy} className="text-gray-400 hover:text-white p-1" title="Copy to clipboard">
              {copied ? <Check size={16} className="text-green-400" /> : <Copy size={16} />}
            </button>
            <button onClick={handleDownload} className="text-gray-400 hover:text-white p-1" title="Download">
              <Download size={16} />
            </button>
            <button onClick={onClose} className="text-gray-400 hover:text-white p-1"><X size={18} /></button>
          </div>
        </div>
        <div className="p-4 space-y-4">
          {/* Hosts */}
          <div>
            <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Mapped Hosts</h4>
            {(volume.hosts ?? []).length === 0 ? (
              <p className="text-gray-600 text-xs">No hosts mapped</p>
            ) : (
              (volume.hosts ?? []).map((h, idx) => {
                const parts = h.split(':')
                return (
                  <span key={idx} className="inline-block mr-2 mb-1 px-2 py-0.5 bg-gray-800 text-gray-300 rounded text-xs font-mono">
                    {parts.length > 1 ? <><span className="text-gray-500">{parts[0]}:</span>{parts[1]}</> : h}
                  </span>
                )
              })
            )}
          </div>

          {/* Details */}
          <div className="bg-gray-800 rounded-lg p-3">
            <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Details</h4>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-gray-500">Size</dt><dd className="text-gray-200">{formatBytes(volume.size_bytes)}</dd>
              <dt className="text-gray-500">Used</dt><dd className="text-gray-200">{formatBytes(volume.used_bytes)}</dd>
              <dt className="text-gray-500">Data Reduction</dt><dd className="text-gray-200">{formatReduction(volume.data_reduction)}</dd>
              <dt className="text-gray-500">Total Reduction</dt><dd className="text-gray-200">{formatReduction(volume.total_reduction)}</dd>
              <dt className="text-gray-500">Snapshots</dt><dd className="text-gray-200">{volume.snapshots ?? 0}</dd>
              <dt className="text-gray-500">Serial</dt><dd className="text-gray-200 font-mono text-xs">{volume.serial || '—'}</dd>
              <dt className="text-gray-500">Created</dt><dd className="text-gray-200">{volume.created || '—'}</dd>
              <dt className="text-gray-500">Updated</dt><dd className="text-gray-200">{volume.last_updated || '—'}</dd>
            </dl>
          </div>

          {/* Protection */}
          <div>
            <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Protection Groups</h4>
            {(volume.protection_groups ?? []).length === 0 ? (
              <p className="text-gray-600 text-xs">None</p>
            ) : (
              (volume.protection_groups ?? []).map((pg) => (
                <span key={pg} className="inline-block mr-2 mb-1 px-2 py-0.5 bg-green-500/10 text-green-400 border border-green-500/20 rounded text-xs">
                  {pg}
                </span>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Notes cell ────────────────────────────────────────────────────────────────

function NotesCell({ volume }: { volume: Volume }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(volume.notes ?? '')
  const inputRef = useRef<HTMLInputElement>(null)
  const qc = useQueryClient()

  const { mutate } = useMutation({
    mutationFn: () => volumesApi.updateNotes(volume.array_name, volume.volume_name, value || null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['volumes'] })
      setEditing(false)
    },
  })

  function startEdit() {
    setValue(volume.notes ?? '')
    setEditing(true)
    setTimeout(() => inputRef.current?.focus(), 50)
  }

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter') mutate()
    if (e.key === 'Escape') setEditing(false)
  }

  function handleBlur() {
    mutate()
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKey}
        onBlur={handleBlur}
        className="w-full bg-gray-800 border border-brand-500 text-xs text-gray-200 rounded px-2 py-1 focus:outline-none"
      />
    )
  }

  return (
    <span
      className="text-xs text-gray-500 cursor-pointer hover:text-gray-300 truncate block max-w-[180px]"
      onClick={startEdit}
      title={volume.notes || 'Click to add note'}
    >
      {volume.notes || <span className="text-gray-700 italic">+ note</span>}
    </span>
  )
}

// ── Pagination ────────────────────────────────────────────────────────────────

function Pagination({ total, offset, limit, onChange }: {
  total: number; offset: number; limit: number; onChange: (offset: number) => void
}) {
  const totalPages = Math.ceil(total / limit)
  const currentPage = Math.floor(offset / limit) + 1
  if (totalPages <= 1) return null

  function goTo(page: number) {
    onChange((page - 1) * limit)
  }

  return (
    <div className="flex items-center justify-between py-3 px-1 border-t border-gray-800 mt-2">
      <span className="text-xs text-gray-500">
        {offset + 1}–{Math.min(offset + limit, total)} of {total.toLocaleString()}
      </span>
      <div className="flex gap-1">
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

export default function Volumes() {
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [arrayFilter, setArrayFilter] = useState('')
  const [vendorFilter, setVendorFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [sortBy, setSortBy] = useState('')
  const [sortDir, setSortDir] = useState('asc')
  const [selectedVolume, setSelectedVolume] = useState<Volume | null>(null)

  // Debounce search input
  useEffect(() => {
    const t = setTimeout(() => { setDebouncedSearch(search); setOffset(0) }, 300)
    return () => clearTimeout(t)
  }, [search])

  // Reset offset when filters change
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
    queryKey: ['volumes', arrayFilter, debouncedSearch, vendorFilter, offset, sortBy, sortDir],
    queryFn: () => volumesApi.list({
      array_name: arrayFilter || undefined,
      search: debouncedSearch || undefined,
      vendor: vendorFilter || undefined,
      limit: PAGE_SIZE,
      offset,
      sort_by: sortBy || undefined,
      sort_dir: sortDir,
    }),
  })

  const volumes = result?.data ?? []
  const total = result?.total ?? 0

  return (
    <div className="space-y-4">
      {/* Header + filters */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-white">
          Volumes <span className="text-gray-500 text-sm ml-2">({total.toLocaleString()})</span>
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
              placeholder="Search volumes..."
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
        ) : volumes.length === 0 ? (
          <p className="text-gray-500 text-sm py-4 text-center">No volumes found</p>
        ) : (
          <>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-700/50 bg-gray-800/40">
                  <SortHeader label="Volume" field="volume_name" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                  <SortHeader label="Array" field="array_name" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                  <SortHeader label="Vendor" field="vendor" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
                  <SortHeader label="Size" field="size" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} align="right" />
                  <SortHeader label="Used" field="used" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} align="right" />
                  <SortHeader label="Reduction" field="data_reduction" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} align="right" />
                  <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Snaps</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Hosts</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {volumes.map((v) => (
                  <tr
                    key={`${v.array_name}/${v.volume_name}`}
                    className="hover:bg-gray-800/30 transition-colors"
                  >
                    <td
                      className="px-4 py-2 font-mono text-xs text-gray-200 cursor-pointer hover:text-brand-400"
                      onClick={() => setSelectedVolume(v)}
                      title="Click for details"
                    >
                      {v.volume_name}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-400">{v.array_name}</td>
                    <td className="px-4 py-2 text-xs">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase border ${
                        VENDOR_COLORS[v.vendor] ?? 'bg-gray-500/10 text-gray-400 border-gray-500/20'
                      }`}>{v.vendor}</span>
                    </td>
                    <td className="px-4 py-2 text-right text-gray-300">{formatBytes(v.size_bytes)}</td>
                    <td className="px-4 py-2 text-right text-gray-300">{formatBytes(v.used_bytes)}</td>
                    <td className="px-4 py-2 text-right text-gray-300">{formatReduction(v.data_reduction)}</td>
                    <td className="px-4 py-2 text-right text-gray-400">{v.snapshots ?? 0}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 max-w-[180px] truncate">
                      {(v.hosts ?? []).slice(0, 3).join(', ') || '—'}
                      {(v.hosts ?? []).length > 3 && ` +${(v.hosts ?? []).length - 3}`}
                    </td>
                    <td className="px-4 py-2 max-w-[200px]">
                      <NotesCell volume={v} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={total} offset={offset} limit={PAGE_SIZE} onChange={setOffset} />
          </>
        )}
      </div>

      {selectedVolume && (
        <VolumeModal volume={selectedVolume} onClose={() => setSelectedVolume(null)} />
      )}
    </div>
  )
}
