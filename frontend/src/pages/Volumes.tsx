import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { volumesApi } from '@/api/volumes'
import { formatBytes, formatReduction } from '@/utils/formatters'

export default function Volumes() {
  const [search, setSearch] = useState('')
  const [arrayFilter, setArrayFilter] = useState('')

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
        <h2 className="text-xl font-semibold text-white">Volumes <span className="text-gray-500 text-sm ml-2">({volumes.length})</span></h2>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Filter by array..."
            value={arrayFilter}
            onChange={(e) => setArrayFilter(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500 w-48"
          />
          <input
            type="text"
            placeholder="Search volume..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500 w-48"
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
                <th className="text-left pb-2">Hosts</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {volumes.map((v) => (
                <tr key={`${v.array_name}/${v.volume_name}`} className="hover:bg-gray-800/30">
                  <td className="py-2 pr-4 font-mono text-xs text-gray-200">{v.volume_name}</td>
                  <td className="py-2 pr-4 text-xs text-gray-400">{v.array_name}</td>
                  <td className="py-2 pr-4 text-right text-gray-300">{formatBytes(v.size_bytes)}</td>
                  <td className="py-2 pr-4 text-right text-gray-300">{formatBytes(v.used_bytes)}</td>
                  <td className="py-2 pr-4 text-right text-gray-300">{formatReduction(v.data_reduction)}</td>
                  <td className="py-2 pr-4 text-right text-gray-400">{v.snapshots ?? 0}</td>
                  <td className="py-2 text-xs text-gray-500">{(v.hosts ?? []).join(', ') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
