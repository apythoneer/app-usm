import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useQuery as useArraysQuery } from '@tanstack/react-query'
import { arraysApi } from '@/api/arrays'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { formatIOPS, formatLatency } from '@/utils/formatters'

export default function Analytics() {
  const { data: arrays = [] } = useArraysQuery({
    queryKey: ['arrays'],
    queryFn: () => arraysApi.list(),
  })

  const [selectedArray, setSelectedArray] = useState('')
  const [hours, setHours] = useState(24)

  const { data: historyResp, isLoading } = useQuery({
    queryKey: ['history', selectedArray, hours],
    queryFn: () => arraysApi.history(selectedArray, hours),
    enabled: !!selectedArray,
  })

  const data = historyResp?.data ?? []

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold text-white">Analytics</h2>

      <div className="flex gap-3">
        <select
          value={selectedArray}
          onChange={(e) => setSelectedArray(e.target.value)}
          className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-brand-500"
        >
          <option value="">Select array...</option>
          {arrays.map((a) => (
            <option key={a.array_name} value={a.array_name}>{a.array_name}</option>
          ))}
        </select>
        {[6, 24, 48, 168].map((h) => (
          <button
            key={h}
            onClick={() => setHours(h)}
            className={`text-xs px-3 py-1.5 rounded border ${hours === h ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'text-gray-400 border-gray-700'}`}
          >
            {h < 24 ? `${h}h` : `${h / 24}d`}
          </button>
        ))}
      </div>

      {isLoading && <p className="text-gray-500 text-sm">Loading history...</p>}

      {data.length > 0 && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          <div className="card">
            <h3 className="text-sm font-semibold text-gray-300 mb-4">IOPS</h3>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={data}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="collected_at" tick={{ fill: '#6b7280', fontSize: 10 }}
                  tickFormatter={(v) => new Date(v).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={formatIOPS} />
                <Tooltip
                  contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8 }}
                  formatter={(v: number) => formatIOPS(v)}
                />
                <Legend />
                <Line type="monotone" dataKey="read_iops"  stroke="#3b82f6" dot={false} name="Read IOPS" />
                <Line type="monotone" dataKey="write_iops" stroke="#8b5cf6" dot={false} name="Write IOPS" />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card">
            <h3 className="text-sm font-semibold text-gray-300 mb-4">Latency</h3>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={data}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="collected_at" tick={{ fill: '#6b7280', fontSize: 10 }}
                  tickFormatter={(v) => new Date(v).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={formatLatency} />
                <Tooltip
                  contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8 }}
                  formatter={(v: number) => formatLatency(v)}
                />
                <Legend />
                <Line type="monotone" dataKey="read_latency_us"  stroke="#10b981" dot={false} name="Read Lat" />
                <Line type="monotone" dataKey="write_latency_us" stroke="#f59e0b" dot={false} name="Write Lat" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {!selectedArray && (
        <div className="card text-center py-12 text-gray-500">
          Select an array above to view historical metrics.
        </div>
      )}
    </div>
  )
}
