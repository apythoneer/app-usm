import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { alertsApi } from '@/api/alerts'
import { severityBg } from '@/utils/formatters'
import type { Severity } from '@/api/types'

const SEVERITIES: Severity[] = ['critical', 'warning', 'info']

export default function Alerts() {
  const [severity, setSeverity] = useState<Severity | undefined>()
  const [showResolved, setShowResolved] = useState(false)

  const { data: alerts = [], isLoading } = useQuery({
    queryKey: ['alerts', severity, showResolved],
    queryFn: () => alertsApi.list({ severity, resolved: showResolved || undefined }),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-white">Alerts</h2>
        <div className="flex gap-2">
          <button
            onClick={() => setSeverity(undefined)}
            className={`text-xs px-3 py-1 rounded border ${!severity ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'text-gray-400 border-gray-700 hover:border-gray-500'}`}
          >All</button>
          {SEVERITIES.map((s) => (
            <button
              key={s}
              onClick={() => setSeverity(s === severity ? undefined : s)}
              className={`text-xs px-3 py-1 rounded border ${severity === s ? severityBg(s) : 'text-gray-400 border-gray-700 hover:border-gray-500'}`}
            >{s}</button>
          ))}
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
            <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} className="accent-brand-500" />
            Resolved
          </label>
        </div>
      </div>

      <div className="card">
        {isLoading ? (
          <p className="text-gray-500 text-sm">Loading...</p>
        ) : alerts.length === 0 ? (
          <p className="text-gray-500 text-sm">No alerts found.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-700/50 bg-gray-800/40">
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Severity</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Array</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Vendor</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Event</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Component</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Opened</th>
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
                    <td className="px-4 py-2 text-xs text-gray-400">{alert.vendor}</td>
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
        )}
      </div>
    </div>
  )
}
