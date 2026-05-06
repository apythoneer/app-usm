import { useState, useRef, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { X, Copy, Download, Pencil, ChevronLeft, ChevronRight, Search, Filter } from 'lucide-react'
import { volumesApi } from '@/api/volumes'
import { arraysApi } from '@/api/arrays'
import type { Volume, ArraySummary } from '@/api/types'
import { formatBytes, formatReduction } from '@/utils/formatters'

const PAGE_SIZE = 50

// ── Volume drilldown modal ────────────────────────────────────────────────────

function VolumeModal({ volume, onClose }: { volume: Volume; onClose: () => void }) {
  function formatText() {
    return [
      `Volume:           ${volume.volume_name}`,
      `Array:            ${volume.array_name}`,
      `Vendor:           ${volume.vendor}`,
      `Serial:           ${volume.serial || '—'}`,
      `Created:          ${volume.created || '—'}`,
      ``,
      `Size:             ${formatBytes(volume.size_bytes)}`,
      `Used:             ${formatBytes(volume.used_bytes)}`,
      `Data Reduction:   ${formatReduction(volume.data_reduction)}`,
      `Total Reduction:  ${formatReduction(volume.total_reduction)}`,
      `Snapshots:        ${volume.snapshots ?? 0}`,
      ``,
      `Connected Hosts:`,
      ...(volume.hosts ?? []).map((h) => `  - ${h}`),
      ``,
      `Host Groups:`,
      ...(volume.host_groups ?? []).map((g) => `  - ${g}`),
      ``,
      `Protection Groups:`,
      ...(volume.protection_groups ?? []).map((p) => `  - ${p}`),
      ``,
      `Notes:            ${volume.notes || '—'}`,
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
    a.download = `${volume.volume_name.replace(/[^a-z0-9]/gi, '_')}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-xl mx-4 max-h-[90vh] overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div>
            <h2 className="text-white font-semibold truncate font-mono">{volume.volume_name}</h2>
            <p className="text-xs text-gray-500 mt-0.5">{volume.array_name} · {volume.vendor}</p>
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
                <span className="font-semibold">{volume.array_name}</span>
                <span className="text-gray-600 text-[10px] ml-1">array</span>
              </div>
              <div className="flex items-start gap-0">
                <span className="text-gray-600 w-4 flex-shrink-0">└─</span>
                <div className="flex items-center gap-1.5 text-cyan-400">
                  <span>▸</span>
                  <span>{volume.volume_name}</span>
                  <span className="text-gray-600 text-[10px]">volume</span>
                </div>
              </div>
              {(volume.hosts ?? []).length === 0 ? (
                <div className="flex items-center gap-0">
                  <span className="text-gray-700 w-8 flex-shrink-0">   └─</span>
                  <span className="text-gray-600 italic">no connected hosts</span>
                </div>
              ) : (
                (volume.hosts ?? []).map((h, idx) => {
                  const isLast = idx === (volume.hosts ?? []).length - 1
                  return (
                    <div key={h} className="flex items-center gap-0">
                      <span className="text-gray-700 w-8 flex-shrink-0">   {isLast ? '└─' : '├─'}</span>
                      <span className="text-green-400">{h}</span>
                      <span className="text-gray-600 text-[10px] ml-1.5">host</span>
                    </div>
                  )
                })
              )}
            </div>
          </div>

          {/* Capacity */}
          <div className="bg-gray-800 rounded-lg p-3">
            <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">Capacity</p>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <dt className="text-gray-500">Size</dt><dd className="text-white">{formatBytes(volume.size_bytes)}</dd>
              <dt className="text-gray-500">Used</dt><dd className="text-white">{formatBytes(volume.used_bytes)}</dd>
              <dt className="text-gray-500">Data reduction</dt><dd className="text-white">{formatReduction(volume.data_reduction)}</dd>
              <dt className="text-gray-500">Total reduction</dt><dd className="text-white">{formatReduction(volume.total_reduction)}</dd>
              <dt className="text-gray-500">Snapshots</dt><dd className="text-white">{volume.snapshots ?? 0}</dd>
              <dt className="text-gray-500">Created</dt><dd className="text-white text-xs">{volume.created || '—'}</dd>
              <dt className="text-gray-500">Serial</dt><dd className="text-white font-mono text-xs">{volume.serial || '—'}</dd>
            </dl>
          </div>

          {/* Connections */}
          <div className="bg-gray-800 rounded-lg p-3">
            <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">Host Connectivity</p>
            {(volume.hosts ?? []).length === 0 ? (
              <p className="text-sm text-gray-500">No connected hosts</p>
            ) : (
              <ul className="space-y-1">
                {(volume.hosts ?? []).map((h) => (
                  <li key={h} className="text-sm text-gray-300 font-mono">{h}</li>
                ))}
              </ul>
            )}
            {(volume.host_groups ?? []).length > 0 && (
              <>
                <p className="text-xs text-gray-500 uppercase tracking-wide mt-3 mb-1">Host Groups</p>
                <ul className="space-y-1">
                  {(volume.host_groups ?? []).map((g) => (
                    <li key={g} className="text-sm text-gray-400">{g}</li>
                  ))}
                </ul>
              </>
            )}
            {(volume.protection_groups ?? []).length > 0 && (
              <>
                <p className="text-xs text-gray-500 uppercase tracking-wide mt-3 mb-1">Protection Groups</p>
                <ul className="space-y-1">
                  {(volume.protection_groups ?? []).map((p) => (
                    <li key={p} className="text-sm text-gray-400">{p}</li>
                  ))}
                </ul>
              </>
            )}
          </div>

          {/* Notes */}
          {volume.notes && (
            <div className="bg-gray-800 rounded-lg p-3">
              <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">Notes</p>
              <p className="text-sm text-gray-300 whitespace-pre-wrap">{volume.notes}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Inline notes cell ─────────────────────────────────────────────────────────

function NotesCell({ volume }: { volume: Volume }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(volume.notes ?? '')
  const inputRef = useRef<HTMLInputElement>(null)
  const savedRef = useRef(false)

  const { mutate: saveNotes } = useMutation({
    mutationFn: (notes: string | null) =>
      volumesApi.updateNotes(volume.array_name, volume.volume_name, notes),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['volumes'] })
      setEditing(false)
    },
  })

  function startEdit() {
    setEditing(true)
    savedRef.current = false
    setValue(volume.notes ?? '')
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter') { savedRef.current = true; saveNotes(value.trim() || null) }
    if (e.key === 'Escape') { setValue(volume.notes ?? ''); setEditing(false) }
  }

  function handleBlur() {
    if (!savedRef.current) saveNotes(value.trim() || null)
    savedRef.current = false
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKey}
        onBlur={handleBlur}
        className="w-full bg-gray-700 border border-brand-500 text-white text-xs rounded px-2 py-1 focus:outline-none"
        placeholder="Type note… Enter=save, Esc=cancel, clear=delete"
      />
    )
  }

  return (
    <div
      className="group flex items-center gap-1.5 cursor-pointer min-h-[24px]"
      onClick={startEdit}
      title="Click to edit note"
    >
      <span className="text-xs text-gray-400 truncate max-w-[160px]">{volume.notes || <span className="text-gray-600">—</span>}</span>
      <Pencil size={11} className="text-gray-600 group-hover:text-gray-400 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
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
        {/* Page number buttons — show max 7 pages */}
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

// ── Volumes page ──────────────────────────────────────────────────────────────

export default function Volumes() {
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [arrayFilter, setArrayFilter] = useState('')
  const [vendorFilter, setVendorFilter] = useState('')
  const [offset, setOffset] = useState(0)
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

  const { data: result, isLoading } = useQuery({
    queryKey: ['volumes', arrayFilter, debouncedSearch, vendorFilter, offset],
    queryFn: () => volumesApi.list({
      array_name: arrayFilter || undefined,
      search: debouncedSearch || undefined,
      vendor: vendorFilter || undefined,
      limit: PAGE_SIZE,
      offset,
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
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Volume</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Array</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Vendor</th>
                  <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Size</th>
                  <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Used</th>
                  <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Reduction</th>
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
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${
                        v.vendor === 'pure' ? 'bg-orange-500/10 text-orange-400 border border-orange-500/20' :
                        v.vendor === 'netapp' ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20' :
                        'bg-gray-500/10 text-gray-400 border border-gray-500/20'
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
