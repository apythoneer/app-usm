import { useQuery } from '@tanstack/react-query'
import { arraysApi } from '@/api/arrays'
import { alertsApi } from '@/api/alerts'
import { formatBytes, formatIOPS, formatLatency, formatPct, usedPctColor, severityBg } from '@/utils/formatters'
import { AlertTriangle, HardDrive, Zap, Activity } from 'lucide-react'

export default function Dashboard() {
  const { data: arrays = [], isLoading: loadingArrays } = useQuery({
    queryKey: ['arrays'],
    queryFn: () => arraysApi.list(),
  })

  const { data: alerts = [] } = useQuery({
    queryKey: ['alerts', 'active'],
    queryFn: () => alertsApi.list({ resolved: false, limit: 10 }),
  })

  const critical = alerts.filter((a) => a.severity === 'critical').length
  const warning  = alerts.filter((a) => a.severity === 'warning').length
  const totalCapacity = arrays.reduce((s, a) => s, 0)

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold text-white">Dashboard</h2>

      {/* Summary cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard icon={HardDrive}   label="Arrays"          value={arrays.length.toString()} color="text-brand-400" />
        <StatCard icon={AlertTriangle} label="Critical Alerts" value={critical.toString()} color="text-red-400" />
        <StatCard icon={AlertTriangle} label="Warnings"        value={warning.toString()}  color="text-yellow-400" />
        <StatCard icon={Activity}    label="Avg Utilization"
          value={
            arrays.length
              ? formatPct(arrays.reduce((s, a) => s + (a.capacity_used_pct ?? 0), 0) / arrays.length)
              : '—'
          }
          color="text-green-400"
        />
      </div>

      {/* Arrays table */}
      <div className="card">
        <h3 className="text-sm font-semibold text-gray-300 mb-4">Storage Arrays</h3>
        {loadingArrays ? (
          <p className="text-gray-500 text-sm">Loading...</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-xs border-b border-gray-800">
                  <th className="text-left pb-2 pr-4">Array</th>
                  <th className="text-left pb-2 pr-4">Vendor</th>
                  <th className="text-right pb-2 pr-4">Total IOPS</th>
                  <th className="text-right pb-2 pr-4">Read Lat</th>
                  <th className="text-right pb-2 pr-4">Write Lat</th>
                  <th className="text-right pb-2">Capacity</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {arrays.map((arr) => (
                  <tr key={arr.array_name} className="hover:bg-gray-800/30 transition-colors">
                    <td className="py-2 pr-4 font-mono text-xs text-gray-200">{arr.array_name}</td>
                    <td className="py-2 pr-4">
                      <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">
                        {arr.vendor}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-right text-gray-300">{formatIOPS(arr.total_iops)}</td>
                    <td className="py-2 pr-4 text-right text-gray-300">{formatLatency(arr.read_latency_us)}</td>
                    <td className="py-2 pr-4 text-right text-gray-300">{formatLatency(arr.write_latency_us)}</td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <div className="w-20 bg-gray-800 rounded-full h-1.5">
                          <div
                            className={`h-1.5 rounded-full ${usedPctColor(arr.capacity_used_pct)}`}
                            style={{ width: `${Math.min(arr.capacity_used_pct ?? 0, 100)}%` }}
                          />
                        </div>
                        <span className="text-gray-300 w-12 text-right">
                          {formatPct(arr.capacity_used_pct)}
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Active alerts */}
      {alerts.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-gray-300 mb-4">Active Alerts</h3>
          <div className="space-y-2">
            {alerts.slice(0, 5).map((alert) => (
              <div key={alert.id} className="flex items-start gap-3 py-2 border-b border-gray-800 last:border-0">
                <span className={`text-xs font-medium px-2 py-0.5 rounded border ${severityBg(alert.severity)}`}>
                  {alert.severity.toUpperCase()}
                </span>
                <div>
                  <p className="text-sm text-gray-200">{alert.array_name}</p>
                  <p className="text-xs text-gray-500">{alert.event}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({
  icon: Icon, label, value, color,
}: { icon: React.ElementType; label: string; value: string; color: string }) {
  return (
    <div className="card flex items-center gap-4">
      <Icon size={20} className={color} />
      <div>
        <p className="text-xs text-gray-500">{label}</p>
        <p className={`text-2xl font-bold ${color}`}>{value}</p>
      </div>
    </div>
  )
}
