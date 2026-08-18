import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer,
} from 'recharts'
import { ChevronDown, ChevronRight, X } from 'lucide-react'
import { arraysApi } from '@/api/arrays'
import type { ArraySummary, TrendPoint } from '@/api/types'
import { formatIOPS, formatLatency, formatBytes, formatPct } from '@/utils/formatters'

// 20-color palette for multi-array lines
const LINE_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#06b6d4', '#f97316', '#84cc16', '#ec4899', '#14b8a6',
  '#a78bfa', '#fb923c', '#4ade80', '#f472b6', '#38bdf8',
  '#fbbf24', '#34d399', '#c084fc', '#fb7185', '#a3e635',
]

const TIME_RANGES = [
  { h: 6, label: '6h' },
  { h: 24, label: '24h' },
  { h: 48, label: '48h' },
  { h: 168, label: '7d' },
  { h: 720, label: '30d' },
]

const TOP_N_OPTIONS = [
  { n: 5, label: 'Top 5' },
  { n: 10, label: 'Top 10' },
  { n: 20, label: 'Top 20' },
  { n: 0, label: 'All' },
]

const bwPerSec = (v: number) => `${formatBytes(v)}/s`

// A metric = how to pull a single number out of a bucketed TrendPoint + how to
// render it. `accessor` returns null when the array/vendor doesn't report it,
// so sparse metrics (e.g. controller_load is NetApp-only) simply leave gaps that
// `connectNulls` bridges.
type Metric = {
  key: string
  label: string
  group: string
  accessor: (p: TrendPoint) => number | null
  format: (v: number) => string
  hint?: string
}

const METRICS: Metric[] = [
  { key: 'iops', label: 'Total IOPS', group: 'Performance',
    accessor: (p) => (p.read_iops ?? 0) + (p.write_iops ?? 0), format: formatIOPS },
  { key: 'read_lat', label: 'Read Latency', group: 'Performance',
    accessor: (p) => p.read_latency_us ?? null, format: formatLatency },
  { key: 'write_lat', label: 'Write Latency', group: 'Performance',
    accessor: (p) => p.write_latency_us ?? null, format: formatLatency },
  { key: 'bandwidth', label: 'Bandwidth', group: 'Performance',
    accessor: (p) => (p.read_bandwidth ?? 0) + (p.write_bandwidth ?? 0), format: bwPerSec },
  { key: 'controller_load', label: 'Controller Load', group: 'Controller',
    accessor: (p) => p.controller_load ?? null, format: formatPct, hint: 'NetApp CPU %' },
  { key: 'nic_util', label: 'NIC Utilization', group: 'Controller',
    accessor: (p) => p.nic_util_pct ?? null, format: formatPct, hint: 'Pure + NetApp' },
  { key: 'san_lat', label: 'SAN Latency', group: 'Latency breakdown',
    accessor: (p) => p.san_latency_us ?? null, format: formatLatency, hint: 'Pure' },
  { key: 'queue_lat', label: 'Queue Latency', group: 'Latency breakdown',
    accessor: (p) => p.queue_latency_us ?? null, format: formatLatency, hint: 'Pure' },
  { key: 'capacity_pct', label: 'Capacity Used', group: 'Capacity',
    accessor: (p) => p.capacity_used_pct ?? null, format: formatPct },
]

const METRIC_GROUPS = [...new Set(METRICS.map((m) => m.group))]

function timeLabel(ms: number, hours: number) {
  const d = new Date(ms)
  if (hours <= 48) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function Analytics() {
  const [hours, setHours] = useState(24)
  const [metricKey, setMetricKey] = useState('iops')
  const [vendorFilter, setVendorFilter] = useState('')
  const [groupFilter, setGroupFilter] = useState('')
  const [topN, setTopN] = useState(10)
  const [arrayFilter, setArrayFilter] = useState<string[]>([])
  const [showArrays, setShowArrays] = useState(false)

  const metric = METRICS.find((m) => m.key === metricKey) ?? METRICS[0]

  // Array list for filters
  const { data: arrays = [] } = useQuery<ArraySummary[]>({
    queryKey: ['arrays'],
    queryFn: () => arraysApi.list(),
  })

  // Fleet-wide TREND — server-bucketed, all arrays in one query
  const { data: trend, isLoading } = useQuery({
    queryKey: ['fleet-trend', hours],
    queryFn: () => arraysApi.fleetTrend(hours),
    refetchInterval: 60_000,
  })

  const allArrayNames: string[] = trend?.arrays ?? []
  const rawData: TrendPoint[] = trend?.data ?? []
  const bucketMin = trend?.bucket_min ?? 0

  const vendors = [...new Set(arrays.map((a) => a.vendor))].sort()
  const groups  = [...new Set(arrays.filter((a) => a.group).map((a) => a.group!))].sort()

  // Rank arrays by average of the SELECTED metric (drives Top-N).
  const metricRank = useMemo(() => {
    const agg: Record<string, { sum: number; n: number }> = {}
    rawData.forEach((pt) => {
      const v = metric.accessor(pt)
      if (v == null) return
      const a = (agg[pt.array_name] ??= { sum: 0, n: 0 })
      a.sum += v; a.n += 1
    })
    return Object.entries(agg)
      .map(([name, { sum, n }]) => [name, n ? sum / n : 0] as const)
      .sort(([, a], [, b]) => b - a)
      .map(([name]) => name)
  }, [rawData, metric])

  const visibleArrays = useMemo(() => {
    let visible = allArrayNames
    if (vendorFilter) {
      const set = new Set(arrays.filter((a) => a.vendor === vendorFilter).map((a) => a.array_name))
      visible = visible.filter((n) => set.has(n))
    }
    if (groupFilter) {
      const set = new Set(arrays.filter((a) => a.group === groupFilter).map((a) => a.array_name))
      visible = visible.filter((n) => set.has(n))
    }
    if (arrayFilter.length > 0) {
      const sel = new Set(arrayFilter)
      visible = visible.filter((n) => sel.has(n))
    }
    if (topN > 0 && arrayFilter.length === 0) {
      const top = new Set(metricRank.slice(0, topN))
      visible = visible.filter((n) => top.has(n))
    }
    return visible
  }, [allArrayNames, arrays, vendorFilter, groupFilter, arrayFilter, topN, metricRank])

  // Pivot: shared bucket grid → { t: ms, [array]: value }. The server already
  // aggregated one row per (array, bucket), so this is a straight assignment —
  // every array lands on the same x-positions and the lines connect.
  const chartData = useMemo(() => {
    const visSet = new Set(visibleArrays)
    const byBucket: Record<number, Record<string, number>> = {}
    rawData.forEach((pt) => {
      if (!visSet.has(pt.array_name)) return
      const v = metric.accessor(pt)
      if (v == null) return
      const t = new Date(pt.bucket).getTime()
      ;(byBucket[t] ??= { t } as Record<string, number>)[pt.array_name] = v
    })
    return Object.values(byBucket).sort((a, b) => (a.t as number) - (b.t as number))
  }, [rawData, visibleArrays, metric])

  // Series actually carrying data for this metric (drop empty ones from legend).
  const activeSeries = useMemo(() => {
    const present = new Set<string>()
    chartData.forEach((row) => {
      Object.keys(row).forEach((k) => { if (k !== 't') present.add(k) })
    })
    return visibleArrays.filter((n) => present.has(n))
  }, [chartData, visibleArrays])

  // Fleet snapshot for the selected metric (latest bucket).
  const snapshot = useMemo(() => {
    const last = chartData[chartData.length - 1]
    if (!last) return null
    const entries = activeSeries
      .map((n) => [n, last[n] as number] as const)
      .filter(([, v]) => typeof v === 'number')
    if (entries.length === 0) return null
    const vals = entries.map(([, v]) => v)
    const peak = entries.reduce((m, e) => (e[1] > m[1] ? e : m))
    return {
      avg: vals.reduce((s, v) => s + v, 0) / vals.length,
      peak: peak[1],
      peakArray: peak[0],
      count: entries.length,
    }
  }, [chartData, activeSeries])

  function toggleArray(name: string) {
    setArrayFilter((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]
    )
  }

  const hasFilters = vendorFilter || groupFilter || arrayFilter.length > 0

  const tooltipStyle = {
    contentStyle: { background: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 },
    labelFormatter: (v: number) => timeLabel(v, hours),
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-white">Analytics</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Fleet metric trends · {bucketMin ? `${bucketMin}-min buckets` : 'live'}
          </p>
        </div>
        <span className="text-xs text-gray-500">
          {activeSeries.length} of {allArrayNames.length} arrays · {rawData.length.toLocaleString()} points
        </span>
      </div>

      {/* Metric selector — grouped */}
      <div className="card p-3">
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {METRIC_GROUPS.map((g) => (
            <div key={g} className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-gray-600">{g}</span>
              <div className="flex flex-wrap gap-1">
                {METRICS.filter((m) => m.group === g).map((m) => (
                  <button
                    key={m.key}
                    onClick={() => setMetricKey(m.key)}
                    title={m.hint}
                    className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                      metricKey === m.key
                        ? 'bg-brand-600/20 text-brand-400 border-brand-500/40'
                        : 'text-gray-400 border-gray-700 hover:border-gray-500'
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Filter toolbar */}
      <div className="card p-3">
        <div className="flex flex-wrap gap-2 items-center">
          <div className="flex gap-1">
            {TIME_RANGES.map(({ h, label }) => (
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

          <span className="text-gray-700">|</span>

          <div className="flex gap-1">
            {TOP_N_OPTIONS.map(({ n, label }) => (
              <button
                key={n}
                onClick={() => setTopN(n)}
                className={`text-xs px-2.5 py-1.5 rounded border transition-colors ${
                  topN === n
                    ? 'bg-brand-600/20 text-brand-400 border-brand-500/30'
                    : 'text-gray-400 border-gray-700 hover:border-gray-500'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <span className="text-gray-700">|</span>

          {vendors.length > 1 && (
            <select
              value={vendorFilter}
              onChange={(e) => setVendorFilter(e.target.value)}
              className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-brand-500"
            >
              <option value="">All vendors</option>
              {vendors.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          )}

          {groups.length > 0 && (
            <select
              value={groupFilter}
              onChange={(e) => setGroupFilter(e.target.value)}
              className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-brand-500"
            >
              <option value="">All groups</option>
              {groups.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          )}

          {hasFilters && (
            <button
              onClick={() => { setVendorFilter(''); setGroupFilter(''); setArrayFilter([]) }}
              className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1"
            >
              <X size={12} /> Clear
            </button>
          )}

          <button
            onClick={() => setShowArrays(!showArrays)}
            className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1 ml-auto"
          >
            {showArrays ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            Arrays ({allArrayNames.length})
          </button>
        </div>

        {showArrays && allArrayNames.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-gray-800">
            {allArrayNames.map((name, i) => {
              const active = arrayFilter.length === 0 || arrayFilter.includes(name)
              const inTopN = topN === 0 || metricRank.indexOf(name) < topN
              return (
                <button
                  key={name}
                  onClick={() => toggleArray(name)}
                  style={{ borderColor: LINE_COLORS[i % LINE_COLORS.length] + '60' }}
                  className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
                    active && inTopN
                      ? 'text-gray-200 bg-gray-800'
                      : 'text-gray-600 bg-transparent opacity-40'
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
      </div>

      {/* Snapshot stat cards for the selected metric */}
      {snapshot && (
        <div className="grid grid-cols-3 gap-3">
          <div className="card py-3">
            <p className="text-[10px] uppercase tracking-wide text-gray-600">Fleet avg · {metric.label}</p>
            <p className="text-lg font-semibold text-white mt-0.5">{metric.format(snapshot.avg)}</p>
          </div>
          <div className="card py-3">
            <p className="text-[10px] uppercase tracking-wide text-gray-600">Peak</p>
            <p className="text-lg font-semibold text-white mt-0.5">{metric.format(snapshot.peak)}</p>
            <p className="text-[10px] text-gray-500 truncate">{snapshot.peakArray}</p>
          </div>
          <div className="card py-3">
            <p className="text-[10px] uppercase tracking-wide text-gray-600">Reporting</p>
            <p className="text-lg font-semibold text-white mt-0.5">{snapshot.count}</p>
            <p className="text-[10px] text-gray-500">arrays with data</p>
          </div>
        </div>
      )}

      {isLoading && <p className="text-gray-500 text-sm">Loading trend…</p>}

      {!isLoading && chartData.length === 0 && (
        <div className="card text-center py-12 text-gray-500">
          No <span className="text-gray-300">{metric.label}</span> data in this range.
          {metric.hint && <span className="block text-xs mt-1">Reported by: {metric.hint}.</span>}
        </div>
      )}

      {chartData.length > 0 && (
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-gray-300">{metric.label}</h3>
            {metric.hint && <span className="text-[11px] text-gray-600">{metric.hint}</span>}
          </div>
          <ResponsiveContainer width="100%" height={380}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={['dataMin', 'dataMax']}
                tick={{ fill: '#6b7280', fontSize: 10 }}
                tickFormatter={(v) => timeLabel(v, hours)}
              />
              <YAxis
                tick={{ fill: '#6b7280', fontSize: 10 }}
                tickFormatter={metric.format}
                width={64}
              />
              <Tooltip {...tooltipStyle} formatter={(v: number) => [metric.format(v), '']} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              {activeSeries.map((name) => (
                <Line
                  key={name}
                  type="monotone"
                  dataKey={name}
                  stroke={LINE_COLORS[allArrayNames.indexOf(name) % LINE_COLORS.length]}
                  dot={false}
                  name={name}
                  strokeWidth={1.5}
                  connectNulls
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
