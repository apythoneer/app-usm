import { useQuery } from '@tanstack/react-query'
import { hostsApi } from '@/api/hosts'

export default function Hosts() {
  const { data: hosts = [], isLoading } = useQuery({
    queryKey: ['hosts'],
    queryFn: () => hostsApi.list(),
  })

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-white">
        Hosts <span className="text-gray-500 text-sm ml-2">({hosts.length})</span>
      </h2>

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
                <tr key={`${h.array_name}/${h.host_name}`} className="hover:bg-gray-800/30">
                  <td className="py-2 pr-4 font-mono text-xs text-gray-200">{h.host_name}</td>
                  <td className="py-2 pr-4 text-xs text-gray-400">{h.array_name}</td>
                  <td className="py-2 pr-4 text-xs text-gray-400">{h.host_group || '—'}</td>
                  <td className="py-2 pr-4 text-xs text-gray-500 font-mono truncate max-w-xs">{h.iqn || '—'}</td>
                  <td className="py-2 pr-4 text-xs text-gray-500 font-mono truncate max-w-xs">{h.wwn || '—'}</td>
                  <td className="py-2 text-right text-gray-400 text-xs">{(h.volumes ?? []).length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
