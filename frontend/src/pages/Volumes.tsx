import { useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { X, Copy, Download, Pencil } from 'lucide-react'
import { volumesApi } from '@/api/volumes'
import type { Volume } from '@/api/types'
import { formatBytes, formatReduction } from '@/utils/formatters'

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

  const { mutate: saveNotes } = useMutation({
    mutationFn: (notes: string) =>
      volumesApi.updateNotes(volume.array_name, volume.volume_name, notes || null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['volumes'] })
      setEditing(false)
    },
  })

  function startEdit() {
    setEditing(true)
    setValue(volume.notes ?? '')
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter') saveNotes(value)
    if (e.key === 'Escape') { setValue(volume.notes ?? ''); setEditing(false) }
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKey}
        onBlur={() => { setValue(volume.notes ?? ''); setEditing(false) }}
        className="w-full bg-gray-700 border border-brand-500 text-white text-xs rounded px-2 py-1 focus:outline-none"
        placeholder="Add note… (Enter to save, Esc to cancel)"
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

// ── Volumes page ──────────────────────────────────────────────────────────────

export default function Volumes() {
  const [search, setSearch] = useState('')
  const [arrayFilter, setArrayFilter] = useState('')
  const [selectedVolume, setSelectedVolume] = useState<Volume | null>(null)

  const { data: volumes = [], isLoading } = useQuery({
    queryKey: ['volumes', arrayFilter, search],
    queryFn: () => volumesApi.list({
      array_name: arrayFilter || undefined,
      search: search || undefined,
    }),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-white">
          Volumes <span className="text-gray-500 text-sm ml-2">({volumes.length})</span>
        </h2>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Filter by array..."
            value={arrayFilter}
            onChange={(e) => setArrayFilter(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500 w-44"
          />
          <input
            type="text"
            placeholder="Search volume..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500 w-44"
          />
        </div>
      </div>

      <div className="card overflow-x-auto">
        {isLoading ? (
          <p className="text-gray-500 text-sm">Loading...</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs border-b border-gray-800">
                <th className="text-left pb-2 pr-4">Volume</th>
                <th className="text-left pb-2 pr-4">Array</th>
                <th className="text-right pb-2 pr-4">Size</th>
                <th className="text-right pb-2 pr-4">Used</th>
                <th className="text-right pb-2 pr-4">Reduction</th>
                <th className="text-right pb-2 pr-4">Snaps</th>
                <th className="text-left pb-2 pr-4">Hosts</th>
                <th className="text-left pb-2">Notes</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {volumes.map((v) => (
                <tr
                  key={`${v.array_name}/${v.volume_name}`}
                  className="hover:bg-gray-800/30 transition-colors"
                >
                  <td
                    className="py-2 pr-4 font-mono text-xs text-gray-200 cursor-pointer hover:text-brand-400"
                    onClick={() => setSelectedVolume(v)}
                    title="Click for details"
                  >
                    {v.volume_name}
                  </td>
                  <td className="py-2 pr-4 text-xs text-gray-400">{v.array_name}</td>
                  <td className="py-2 pr-4 text-right text-gray-300">{formatBytes(v.size_bytes)}</td>
                  <td className="py-2 pr-4 text-right text-gray-300">{formatBytes(v.used_bytes)}</td>
                  <td className="py-2 pr-4 text-right text-gray-300">{formatReduction(v.data_reduction)}</td>
                  <td className="py-2 pr-4 text-right text-gray-400">{v.snapshots ?? 0}</td>
                  <td className="py-2 pr-4 text-xs text-gray-500 max-w-[180px] truncate">
                    {(v.hosts ?? []).slice(0, 3).join(', ') || '—'}
                    {(v.hosts ?? []).length > 3 && ` +${(v.hosts ?? []).length - 3}`}
                  </td>
                  <td className="py-2 max-w-[200px]">
                    <NotesCell volume={v} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selectedVolume && (
        <VolumeModal volume={selectedVolume} onClose={() => setSelectedVolume(null)} />
      )}
    </div>
  )
}
