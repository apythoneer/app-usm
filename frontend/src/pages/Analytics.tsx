import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer,
} from 'recharts'
import { arraysApi } from '@/api/arrays'
import type { ArraySummary, MetricsHistoryPoint } from '@/api/types'
import { formatIOPS, formatLatency } from '@/utils/formatters'

// 20-color palette for multi-array lines
const LINE_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#06b6d4', '#f97316', '#84cc16', '#ec4899', '#14b8a6',
  '#a78bfa', '#fb923c', '#4ade80', '#f472b6', '#38bdf8',
  '#fbbf24', '#34d399', '#c084fc', '#fb7185', '#a3e635',
]

function timeLabel(v: string, hours: number) {
  const d = new Date(v)
  if (hours <= 48) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function Analytics() {
  const [hours, setHours] = useState(24)
  const [vendorFilter, setVendorFilter] = useState('')
  const [groupFilter, setGroupFilter] = useState('')
  const [arrayFilter, setArrayFilter] = useState<string[]>([])

  // Array list for filters
  const { data: arrays = [] } = useQuery<ArraySummary[]>({
    queryKey: ['arrays'],
    queryFn: () => arraysApi.list(),
  })

  // Fleet-wide history — all arrays in one query
  const { data: histResp, isLoading } = useQuery({
    queryKey: ['fleet-history', hours],
    queryFn: () => arraysApi.fleetHistory(hours),
    refetchInterval: 60_000,
  })

  const allArrayNames: string[] = histResp?.arrays ?? []
  const rawData: MetricsHistoryPoint[] = histResp?.data ?? []

  // Unique vendors and groups for filter dropdowns
  const vendors = [...new Set(arrays.map((a) => a.vendor))].sort()
  const groups  = [...new Set(arrays.filter((a) => a.group).map((a) => a.group!))].sort()

  // Build set of visible array names after filters
  const visibleArrays = useMemo(() => {
    let visible = allArrayNames
    if (vendorFilter) {
      const vendorSet = new Set(arrays.filter((a) => a.vendor === vendorFilter).map((a) => a.array_name))
      visible = visible.filter((n) => vendorSet.has(n))
    }
    if (groupFilter) {
      const groupSet = new Set(arrays.filter((a) => a.group === groupFilter).map((a) => a.array_name))
      visible = visible.filter((n) => groupSet.has(n))
    }
    if (arrayFilter.length > 0) {
      const sel = new Set(arrayFilter)
      visible = visible.filter((n) => sel.has(n))
    }
    return visible
  }, [allArrayNames, arrays, vendorFilter, groupFilter, arrayFilter])

  // Pivot data: [{collected_at, arrayName: value, ...}]
  const iopsData = useMemo(() => {
    const byTime: Record<string, Record<string, number>> = {}
    rawData.forEach((pt) => {
      if (!visibleArrays.includes(pt.array_name)) return
      const t = pt.collected_at
      if (!byTime[t]) byTime[t] = { collected_at: t as unknown as number }
      byTime[t][pt.array_name] = (pt.read_iops ?? 0) + (pt.write_iops ?? 0)
    })
    return Object.values(byTime).sort((a, b) =>
      String(a.collected_at).localeCompare(String(b.collected_at))
    )
  }, [rawData, visibleArrays])

  const latData = useMemo(() => {
    const byTime: Record<string, Record<string, number>> = {}
    rawData.forEach((pt) => {
      if (!visibleArrays.includes(pt.array_name)) return
      const t = pt.collected_at
      if (!byTime[t]) byTime[t] = { collected_at: t as unknown as number }
      if (pt.read_latency_us != null) byTime[t][pt.array_name] = pt.read_latency_us
    })
    return Object.values(byTime).sort((a, b) =>
      String(a.collected_at).localeCompare(String(b.collected_at))
    )
  }, [rawData, visibleArrays])

  const writeLatData = useMemo(() => {
    const byTime: Record<string, Record<string, number>> = {}
    rawData.forEach((pt) => {
      if (!visibleArrays.includes(pt.array_name)) return
      const t = pt.collected_at
      if (!byTime[t]) byTime[t] = { collected_at: t as unknown as number }
      if (pt.write_latency_us != null) byTime[t][pt.array_name] = pt.write_latency_us
    })
    return Object.values(byTime).sort((a, b) =>
      String(a.collected_at).localeCompare(String(b.collected_at))
    )
  }, [rawData, visibleArrays])

  function toggleArray(name: string) {
    setArrayFilter((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]
    )
  }

  const tooltipStyle = {
    contentStyle: { background: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 },
    labelFormatter: (v: string) => timeLabel(v, hours),
  }

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold text-white">Analytics</h2>

      {/* Filters row */}
      <div className="flex flex-wrap gap-3 items-center">
        {/* Time range */}
        <div className="flex gap-1">
          {[{ h: 6, label: '6h' }, { h: 24, label: '24h' }, { h: 48, label: '48h' }, { h: 168, label: '7d' }].map(({ h, label }) => (
            <button
              key={h}
              onClick={() => setHours(h)}
              className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                hours === h
                  ? 'bg-brand-600/20 text-brand-400 border-brand-500/30'
                  : 'text-gray-400 border-gray-700 hover:border-gray-500'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Vendor filter */}
        {vendors.length > 1 && (
          <select
            value={vendorFilter}
            onChange={(e) => setVendorFilter(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500"
          >
            <option value="">All vendors</option>
            {vendors.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        )}

        {/* Group filter */}
        {groups.length > 0 && (
          <select
            value={groupFilter}
            onChange={(e) => setGroupFilter(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500"
          >
            <option value="">All groups</option>
            {groups.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        )}

        {/* Clear filters */}
        {(vendorFilter || groupFilter || arrayFilter.length > 0) && (
          <button
            onClick={() => { setVendorFilter(''); setGroupFilter(''); setArrayFilter([]) }}
            className="text-xs text-gray-500 hover:text-gray-300 underline"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Array toggle chips */}
      {allArrayNames.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {allArrayNames.map((name, i) => {
            const active = arrayFilter.length === 0 || arrayFilter.includes(name)
            return (
              <button
                key={name}
                onClick={() => toggleArray(name)}
                style={{ borderColor: LINE_COLORS[i % LINE_COLORS.length] + '60' }}
                className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
                  active
                    ? 'text-gray-200 bg-gray-800'
                    : 'text-gray-600 bg-transparent opacity-50'
                }`}
              >
                <span
                  className="inline-block w-2 h-2 rounded-full mr-1"
                  style={{ background: LINE_COLORS[i % LINE_COLORS.length] }}
                />
                {name}
              </button>
            )
          })}
        </div>
      )}

      {isLoading && <p className="text-gray-500 text-sm">Loading history…</p>}

      {!isLoading && iopsData.length === 0 && (
        <div className="card text-center py-12 text-gray-500">
          No history data yet — data appears once collectors have run at least once.
        </div>
      )}

      {iopsData.length > 0 && (
        <div className="space-y-6">
          {/* IOPS — full width */}
          <div className="card">
            <h3 className="text-sm font-semibold text-gray-300 mb-4">Total IOPS (Read + Write)</h3>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={iopsData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis
                  dataKey="collected_at"
                  tick={{ fill: '#6b7280', fontSize: 10 }}
                  tickFormatter={(v) => timeLabel(v, hours)}
                />
                <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={formatIOPS} width={55} />
                <Tooltip {...tooltipStyle} formatter={(v: number) => [formatIOPS(v), '']} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {visibleArrays.map((name) => (
                  <Line
                    key={name}
                    type="monotone"
                    dataKey={name}
                    stroke={LINE_COLORS[allArrayNames.indexOf(name) % LINE_COLORS.length]}
                    dot={false}
                    name={name}
                    strokeWidth={1.5}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Latency — Read & Write side by side */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            <div className="card">
              <h3 className="text-sm font-semibold text-gray-300 mb-4">Read Latency</h3>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={latData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                  <XAxis
                    dataKey="collected_at"
                    tick={{ fill: '#6b7280', fontSize: 10 }}
                    tickFormatter={(v) => timeLabel(v, hours)}
                  />
                  <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={formatLatency} width={60} />
                  <Tooltip {...tooltipStyle} formatter={(v: number) => [formatLatency(v), '']} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  {visibleArrays.map((name) => (
                    <Line
                      key={name}
                      type="monotone"
                      dataKey={name}
                      stroke={LINE_COLORS[allArrayNames.indexOf(name) % LINE_COLORS.length]}
                      dot={false}
                      name={name}
                      strokeWidth={1.5}
                      connectNulls
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div className="card">
              <h3 className="text-sm font-semibold text-gray-300 mb-4">Write Latency</h3>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={writeLatData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                  <XAxis
                    dataKey="collected_at"
                    tick={{ fill: '#6b7280', fontSize: 10 }}
                    tickFormatter={(v) => timeLabel(v, hours)}
                  />
                  <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={formatLatency} width={60} />
                  <Tooltip {...tooltipStyle} formatter={(v: number) => [formatLatency(v), '']} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  {visibleArrays.map((name) => (
                    <Line
                      key={name}
                      type="monotone"
                      dataKey={name}
                      stroke={LINE_COLORS[allArrayNames.indexOf(name) % LINE_COLORS.length]}
                      dot={false}
                      name={name}
                      strokeWidth={1.5}
                      connectNulls
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
