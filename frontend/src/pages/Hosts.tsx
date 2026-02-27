import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X, Copy, Download } from 'lucide-react'
import { hostsApi } from '@/api/hosts'
import type { Host } from '@/api/types'
import { formatBytes } from '@/utils/formatters'

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

// ── Hosts page ────────────────────────────────────────────────────────────────

export default function Hosts() {
  const [search, setSearch] = useState('')
  const [arrayFilter, setArrayFilter] = useState('')
  const [selectedHost, setSelectedHost] = useState<Host | null>(null)

  const { data: hosts = [], isLoading } = useQuery({
    queryKey: ['hosts', arrayFilter, search],
    queryFn: () => hostsApi.list({
      array_name: arrayFilter || undefined,
      search: search || undefined,
    }),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-white">
          Hosts <span className="text-gray-500 text-sm ml-2">({hosts.length})</span>
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
            placeholder="Search host..."
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
                <th className="text-left pb-2 pr-4">Host</th>
                <th className="text-left pb-2 pr-4">Array</th>
                <th className="text-left pb-2 pr-4">Host Group</th>
                <th className="text-left pb-2 pr-4">iSCSI IQN</th>
                <th className="text-left pb-2 pr-4">FC WWN</th>
                <th className="text-right pb-2">Volumes</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {hosts.map((h) => (
                <tr
                  key={`${h.array_name}/${h.host_name}`}
                  className="hover:bg-gray-800/30 cursor-pointer transition-colors"
                  onClick={() => setSelectedHost(h)}
                >
                  <td className="py-2 pr-4 font-mono text-xs text-gray-200 hover:text-brand-400">{h.host_name}</td>
                  <td className="py-2 pr-4 text-xs text-gray-400">{h.array_name}</td>
                  <td className="py-2 pr-4 text-xs text-gray-400">{h.host_group || '—'}</td>
                  <td className="py-2 pr-4 text-xs text-gray-500 font-mono truncate max-w-[180px]">{h.iqn || '—'}</td>
                  <td className="py-2 pr-4 text-xs text-gray-500 font-mono truncate max-w-[180px]">{h.wwn || '—'}</td>
                  <td className="py-2 text-right text-gray-400 text-xs">{(h.volumes ?? []).length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selectedHost && (
        <HostModal host={selectedHost} onClose={() => setSelectedHost(null)} />
      )}
    </div>
  )
}
