import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, TrendingUp, TrendingDown, Sparkles } from 'lucide-react'

import {
  BarChart, Bar, LineChart, Line, ReferenceLine,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, Cell,
  ResponsiveContainer,
} from 'recharts'


import { arraysApi } from '@/api/arrays'
import { volumesApi } from '@/api/volumes'
import { formatTB as tb } from '@/utils/formatters'
import type {
  CapacityBreakdown, CapacityBucket, VendorBucket, CloudBucket, VendorCloudBucket,
  DailyTrendResponse, TopGrowersResponse, ArrayGrowth, ArraySummary,
  VolumeHistoryCoverage, VolumeGrowth, TopVolumeGrowersResponse, Volume,
  PaginatedResponse, CapacityForecast,
} from '@/api/types'


const TREND_RANGES = [
  { d: 7, label: '7d' },
  { d: 14, label: '14d' },
  { d: 30, label: '30d' },
  { d: 90, label: '90d' },
  { d: 180, label: '180d' },
  { d: 365, label: '1y' },
]

// Default window. History only began accumulating in mid-June 2026, so a short
// window fills the charts today; the longer ranges become useful as data grows.
const DEFAULT_TREND_DAYS = 90


function trendDate(v: string) {
  const d = new Date(v)
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}


const tooltipStyle = {
  contentStyle: { background: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 },
  // Force light text — recharts otherwise colors each item by its series color
  // (e.g. the dark-gray "Free" series), which is unreadable on the dark tooltip.
  itemStyle: { color: '#e5e7eb' },
  labelStyle: { color: '#9ca3af' },
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-2xl font-semibold text-white mt-1">{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  )
}

function UtilBar({ pct }: { pct: number }) {
  const color = pct >= 90 ? 'bg-red-500' : pct >= 75 ? 'bg-amber-500' : 'bg-brand-500'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden min-w-[60px]">
        <div className={`h-full ${color}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className="text-xs text-gray-400 w-10 text-right">{pct.toFixed(0)}%</span>
    </div>
  )
}

function BreakdownTable<T extends CapacityBucket>({
  title, label, rows, keyField,
}: {
  title: string
  label: string
  rows: T[]
  keyField: (r: T) => string
}) {
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">{title}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-gray-500 border-b border-gray-800">
              <th className="py-2 pr-4">{label}</th>
              <th className="py-2 pr-4 text-right">Arrays</th>
              <th className="py-2 pr-4 text-right">Usable</th>
              <th className="py-2 pr-4 text-right">Used</th>
              <th className="py-2 pr-4 text-right">Free</th>
              <th className="py-2 pr-4 w-40">Utilization</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={keyField(r)} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                <td className="py-2 pr-4 text-gray-200 font-medium capitalize">{keyField(r)}</td>
                <td className="py-2 pr-4 text-right text-gray-400">{r.arrays}</td>
                <td className="py-2 pr-4 text-right text-gray-300">{tb(r.usable_tb)}</td>
                <td className="py-2 pr-4 text-right text-gray-300">{tb(r.used_tb)}</td>
                <td className="py-2 pr-4 text-right text-gray-300">{tb(r.free_tb)}</td>
                <td className="py-2 pr-4"><UtilBar pct={r.utilization_pct} /></td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={6} className="py-6 text-center text-gray-600">No data</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Fleet growth / decline summary (daily_stats) ──────────────────────────────

// Linear least-squares slope of y over index 0..n-1 → average units per step.
function linregSlope(ys: number[]): number {
  const n = ys.length
  if (n < 2) return 0
  const xMean = (n - 1) / 2
  const yMean = ys.reduce((a, b) => a + b, 0) / n
  let num = 0
  let den = 0
  for (let i = 0; i < n; i++) {
    num += (i - xMean) * (ys[i] - yMean)
    den += (i - xMean) * (i - xMean)
  }
  return den === 0 ? 0 : num / den
}

function GrowthStat({
  label, value, sub, tone = 'neutral',
}: {
  label: string
  value: string
  sub?: string
  tone?: 'up' | 'down' | 'neutral'
}) {
  const color =
    tone === 'up' ? 'text-emerald-400' : tone === 'down' ? 'text-red-400' : 'text-white'
  return (
    <div className="card">
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`text-2xl font-semibold mt-1 flex items-center gap-1 ${color}`}>
        {tone === 'up' && <TrendingUp size={18} />}
        {tone === 'down' && <TrendingDown size={18} />}
        {value}
      </p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  )
}

function FleetGrowthSummary() {
  const [days, setDays] = useState(DEFAULT_TREND_DAYS)

  const { data, isLoading } = useQuery<DailyTrendResponse>({
    queryKey: ['daily-trend', days],
    queryFn: () => arraysApi.dailyTrend(days),
    refetchInterval: 300_000,
  })

  const points = data?.data ?? []

  const proj = data?.projection
  const used = points.map((p) => p.total_used_tb)
  const first = used.length ? used[0] : 0
  const last = used.length ? used[used.length - 1] : 0

  // Net Change reflects the SELECTED chart window (first vs last visible point),
  // so it responds to the range buttons as expected.
  const deltaTb = +(last - first).toFixed(2)
  const deltaPct = first > 0 ? (deltaTb / first) * 100 : null
  const spanDays = points.length > 1 ? points.length - 1 : 0
  const growing = deltaTb > 0.01

  // Forward-looking projection comes from the server's fixed-window fit, NOT the
  // selected range — so Avg Rate / Projected Full stay stable when you switch
  // ranges (previously a 7d vs 14d window swung "days to full" wildly).
  const perDay = proj ? proj.avg_rate_tb_per_day : spanDays > 0 ? linregSlope(used) : 0
  const trend = proj ? proj.trend : deltaTb > 0.01 ? 'growing' : deltaTb < -0.01 ? 'declining' : 'stable'
  const projWindow = proj?.window_days ?? 0

  const lastUsable = proj ? proj.usable_tb : points.length ? points[points.length - 1].total_capacity_tb : 0
  const headroomTb = proj ? proj.headroom_tb : Math.max(lastUsable - last, 0)
  const daysToFull = proj ? proj.days_to_full : perDay > 0.0001 ? Math.round(headroomTb / perDay) : null
  const projectedFullDate = proj ? proj.projected_full_date : null
  const lastCollected = data?.last_collected ?? null

  // Per-step day-over-day change, for the colored gain/loss bar chart.
  const deltaSeries = points.map((p, i) => ({
    date: p.date,
    change: i === 0 ? 0 : +(p.total_used_tb - points[i - 1].total_used_tb).toFixed(2),
  }))

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-300">Storage Consumption — Growth &amp; Decline</h3>
          {points.length > 0 && (
            <p className="text-xs text-gray-500 mt-0.5">
              {trendDate(points[0].date)} → {trendDate(points[points.length - 1].date)} · {points.length} daily points
              {lastCollected && (
                <span className="ml-2 text-gray-600">· updated {trendDate(lastCollected)}</span>
              )}
            </p>
          )}
        </div>
        <div className="flex gap-1">
          {TREND_RANGES.map(({ d, label }) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                days === d
                  ? 'bg-brand-600/20 text-brand-400 border-brand-500/30'
                  : 'text-gray-400 border-gray-700 hover:border-gray-500'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <p className="text-gray-500 text-sm">Loading growth…</p>}

      {!isLoading && points.length < 2 && (
        <div className="text-center py-10 text-gray-600 text-sm">
          Not enough daily history in this window yet to compute growth.
        </div>
      )}

      {points.length >= 2 && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <GrowthStat
              label={`Net Change (${spanDays}d)`}
              value={`${growing ? '+' : ''}${tb(deltaTb)}`}
              sub={deltaPct != null ? `${deltaPct > 0 ? '+' : ''}${deltaPct.toFixed(1)}%` : undefined}
              tone={growing ? 'up' : 'down'}
            />
            <GrowthStat
              label="Avg Rate"
              value={`${perDay >= 0 ? '+' : ''}${tb(Math.abs(perDay))}/day`}
              sub={projWindow > 0 ? `${projWindow}d trend · ${perDay >= 0 ? 'consuming' : 'reclaiming'}` : (perDay >= 0 ? 'consuming' : 'reclaiming')}
              tone={perDay >= 0 ? 'up' : 'down'}
            />
            <GrowthStat label="Free Headroom" value={tb(headroomTb)} sub={`of ${tb(lastUsable)} usable`} />
            <GrowthStat
              label="Projected Full"
              value={
                daysToFull != null
                  ? `~${daysToFull}d`
                  : trend === 'declining' || trend === 'stable'
                  ? 'N/A'
                  : '—'
              }
              sub={
                daysToFull != null
                  ? projectedFullDate
                    ? `by ${trendDate(projectedFullDate)} · ${projWindow}d basis`
                    : `at current rate · ${projWindow}d basis`
                  : trend === 'declining'
                  ? 'reclaiming — not filling'
                  : 'stable — not filling'
              }
              tone={daysToFull != null && daysToFull < 90 ? 'down' : 'neutral'}
            />
          </div>

          <p className="text-xs text-gray-500 mb-1">Day-over-day change (used capacity)</p>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={deltaSeries}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis dataKey="date" tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={trendDate} />
              <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={(v: number) => `${v} TB`} width={64} />
              <Tooltip
                {...tooltipStyle}
                labelFormatter={trendDate}
                formatter={(v: number) => [`${v >= 0 ? '+' : ''}${tb(v)}`, 'Change']}
              />
              <ReferenceLine y={0} stroke="#6b7280" />
              <Bar dataKey="change">
                {deltaSeries.map((d, i) => (
                  <Cell key={i} fill={d.change >= 0 ? '#10b981' : '#ef4444'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  )
}

// ── Fleet capacity over time (daily_stats) ────────────────────────────────────

function FleetTrendChart() {

  const [days, setDays] = useState(DEFAULT_TREND_DAYS)


  const { data, isLoading } = useQuery<DailyTrendResponse>({
    queryKey: ['daily-trend', days],
    queryFn: () => arraysApi.dailyTrend(days),
    refetchInterval: 300_000,
  })

  const points = data?.data ?? []

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-gray-300">Fleet Capacity Over Time</h3>
        <div className="flex gap-1">
          {TREND_RANGES.map(({ d, label }) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                days === d
                  ? 'bg-brand-600/20 text-brand-400 border-brand-500/30'
                  : 'text-gray-400 border-gray-700 hover:border-gray-500'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <p className="text-gray-500 text-sm">Loading trend…</p>}

      {!isLoading && points.length === 0 && (
        <div className="text-center py-10 text-gray-600 text-sm">
          No daily history yet — the fleet trend builds up once daily stats have run.
        </div>
      )}

      {points.length > 0 && (
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={points}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="date" tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={trendDate} />
            <YAxis
              yAxisId="tb"
              tick={{ fill: '#6b7280', fontSize: 10 }}
              tickFormatter={(v: number) => `${v} TB`}
              width={64}
            />
            <YAxis
              yAxisId="pct"
              orientation="right"
              domain={[0, 100]}
              tick={{ fill: '#6b7280', fontSize: 10 }}
              tickFormatter={(v: number) => `${v}%`}
              width={44}
            />
            <Tooltip {...tooltipStyle} labelFormatter={trendDate} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Line
              yAxisId="tb"
              type="monotone"
              dataKey="total_capacity_tb"
              name="Usable TB"
              stroke="#6b7280"
              strokeDasharray="4 3"
              dot={false}
              strokeWidth={1.5}
            />
            <Line
              yAxisId="tb"
              type="monotone"
              dataKey="total_used_tb"
              name="Used TB"
              stroke="#3b82f6"
              dot={false}
              strokeWidth={2}
            />
            <Line
              yAxisId="pct"
              type="monotone"
              dataKey="avg_utilization_pct"
              name="Avg Util %"
              stroke="#f59e0b"
              dot={false}
              strokeWidth={1.5}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// ── Per-array growth detail ───────────────────────────────────────────────────

function ArrayGrowthDetail({ arrays }: { arrays: ArraySummary[] }) {
  const [selected, setSelected] = useState('')

  const { data, isLoading } = useQuery<ArrayGrowth>({
    queryKey: ['array-growth', selected],
    queryFn: () => arraysApi.arrayGrowth(selected, 12),
    enabled: !!selected,
  })

  const ytd = data?.ytd
  const trend = data?.trend ?? []
  const growthPositive = (ytd?.growth_tb ?? 0) >= 0

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-gray-300">Per-Array Growth</h3>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-brand-500"
        >
          <option value="">Select an array…</option>
          {[...arrays]
            .sort((a, b) => a.array_name.localeCompare(b.array_name))
            .map((a) => (
              <option key={a.array_name} value={a.array_name}>{a.array_name}</option>
            ))}
        </select>
      </div>

      {!selected && (
        <p className="text-gray-600 text-xs">
          Pick an array above to see its YTD growth and monthly capacity trend.
        </p>
      )}

      {selected && isLoading && <p className="text-gray-500 text-sm">Loading growth…</p>}

      {selected && !isLoading && data && (
        <div className="space-y-4">
          {ytd ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard label="YTD Start" value={tb(ytd.start_used_tb)} sub={trendDate(ytd.start_date)} />
              <StatCard label="Current Used" value={tb(ytd.current_used_tb)} />
              <div className="card">
                <p className="text-xs text-gray-500">YTD Change</p>
                <p className={`text-2xl font-semibold mt-1 flex items-center gap-1 ${growthPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                  {growthPositive ? <TrendingUp size={18} /> : <TrendingDown size={18} />}
                  {growthPositive ? '+' : ''}{tb(ytd.growth_tb)}
                </p>
              </div>
              <StatCard
                label="YTD %"
                value={ytd.growth_pct != null ? `${ytd.growth_pct > 0 ? '+' : ''}${ytd.growth_pct}%` : '—'}
              />
            </div>
          ) : (
            <p className="text-gray-600 text-sm">No YTD baseline sample available for this array yet.</p>
          )}

          {trend.length > 0 && (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="date" tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={trendDate} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={(v: number) => `${v} TB`} width={64} />
                <Tooltip {...tooltipStyle} labelFormatter={trendDate} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="usable_tb" name="Usable TB" stroke="#6b7280" strokeDasharray="4 3" dot={false} strokeWidth={1.5} />
                <Line type="monotone" dataKey="used_tb" name="Used TB" stroke="#3b82f6" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      )}
    </div>
  )
}

// ── Top growers / shrinkers ───────────────────────────────────────────────────

function TopMoversTable() {
  const [days, setDays] = useState(DEFAULT_TREND_DAYS)


  const { data, isLoading } = useQuery<TopGrowersResponse>({
    queryKey: ['top-growers', days],
    queryFn: () => arraysApi.topGrowers(days, 20),
    refetchInterval: 300_000,
  })

  const rows = data?.data ?? []

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-300">Top Growers & Shrinkers</h3>
        <div className="flex gap-1">
          {TREND_RANGES.map(({ d, label }) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                days === d
                  ? 'bg-brand-600/20 text-brand-400 border-brand-500/30'
                  : 'text-gray-400 border-gray-700 hover:border-gray-500'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <p className="text-gray-500 text-sm">Loading…</p>}

      {!isLoading && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 border-b border-gray-800">
                <th className="py-2 pr-4">Array</th>
                <th className="py-2 pr-4">Vendor</th>
                <th className="py-2 pr-4 text-right">Start Used</th>
                <th className="py-2 pr-4 text-right">Current Used</th>
                <th className="py-2 pr-4 text-right">Change</th>
                <th className="py-2 pr-4 text-right">Change %</th>
                <th className="py-2 pr-4 w-40">Utilization</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const up = r.delta_tb >= 0
                return (
                  <tr key={r.array_name} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="py-2 pr-4 text-gray-200 font-medium">{r.array_name}</td>
                    <td className="py-2 pr-4 text-gray-400 capitalize">{r.vendor}</td>
                    <td className="py-2 pr-4 text-right text-gray-300">{tb(r.start_used_tb)}</td>
                    <td className="py-2 pr-4 text-right text-gray-300">{tb(r.current_used_tb)}</td>
                    <td className={`py-2 pr-4 text-right font-medium ${up ? 'text-emerald-400' : 'text-red-400'}`}>
                      <span className="inline-flex items-center gap-1 justify-end">
                        {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                        {up ? '+' : ''}{tb(r.delta_tb)}
                      </span>
                    </td>
                    <td className={`py-2 pr-4 text-right ${up ? 'text-emerald-400' : 'text-red-400'}`}>
                      {r.delta_pct != null ? `${r.delta_pct > 0 ? '+' : ''}${r.delta_pct}%` : '—'}
                    </td>
                    <td className="py-2 pr-4">
                      {r.utilization_pct != null ? <UtilBar pct={r.utilization_pct} /> : '—'}
                    </td>
                  </tr>
                )
              })}
              {rows.length === 0 && (
                <tr><td colSpan={7} className="py-6 text-center text-gray-600">No history in this window yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Per-volume growth (backed by volumes_history) ─────────────────────────────

function VolumeGrowthSection({ arrays }: { arrays: ArraySummary[] }) {
  const [days, setDays] = useState(DEFAULT_TREND_DAYS)

  const [selectedArray, setSelectedArray] = useState('')
  const [selectedVolume, setSelectedVolume] = useState('')

  // Coverage banner: how much per-volume history actually exists yet.
  const { data: coverage } = useQuery<VolumeHistoryCoverage>({
    queryKey: ['volume-history-coverage'],
    queryFn: () => arraysApi.volumeHistoryCoverage(),
    refetchInterval: 300_000,
  })

  // Top movers across the fleet (or scoped to selected array).
  const { data: movers, isLoading: moversLoading } = useQuery<TopVolumeGrowersResponse>({
    queryKey: ['top-volume-growers', days, selectedArray],
    queryFn: () => arraysApi.topVolumeGrowers(days, 25, selectedArray || undefined),
    refetchInterval: 300_000,
  })

  // Volume picker for the selected array (uses the existing paginated volumes API).
  const { data: volPage } = useQuery<PaginatedResponse<Volume>>({
    queryKey: ['volumes-for-growth', selectedArray],
    queryFn: () => volumesApi.list({ array_name: selectedArray, limit: 500, sort_by: 'size_bytes', sort_dir: 'desc' }),
    enabled: !!selectedArray,
  })

  // Per-volume trend for the selected volume.
  const { data: growth, isLoading: growthLoading } = useQuery<VolumeGrowth>({
    queryKey: ['volume-growth', selectedArray, selectedVolume, days],
    queryFn: () => arraysApi.volumeGrowth(selectedArray, selectedVolume, days),
    enabled: !!selectedArray && !!selectedVolume,
  })

  const moverRows = movers?.data ?? []
  const trend = growth?.trend ?? []
  const g = growth?.growth
  const up = (g?.growth_tb ?? 0) >= 0

  const hasHistory = (coverage?.rows_total ?? 0) > 0

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-300">Volume Consumption Growth</h3>
          {coverage && (
            <p className="text-xs text-gray-500 mt-0.5">
              {hasHistory
                ? `History: ${coverage.distinct_days} day(s) · ${coverage.rows_total.toLocaleString()} samples · since ${coverage.first_seen ? trendDate(coverage.first_seen) : '—'}`
                : 'No per-volume history yet — it begins accumulating on the next collection cycle.'}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={selectedArray}
            onChange={(e) => { setSelectedArray(e.target.value); setSelectedVolume('') }}
            className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-brand-500"
          >
            <option value="">All arrays</option>
            {[...arrays]
              .sort((a, b) => a.array_name.localeCompare(b.array_name))
              .map((a) => (
                <option key={a.array_name} value={a.array_name}>{a.array_name}</option>
              ))}
          </select>
          <div className="flex gap-1">
            {TREND_RANGES.map(({ d, label }) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                  days === d
                    ? 'bg-brand-600/20 text-brand-400 border-brand-500/30'
                    : 'text-gray-400 border-gray-700 hover:border-gray-500'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Per-volume trend (when a specific volume is selected) */}
      {selectedArray && (
        <div className="mb-5">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs text-gray-500">Volume:</span>
            <select
              value={selectedVolume}
              onChange={(e) => setSelectedVolume(e.target.value)}
              className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-brand-500 max-w-md"
            >
              <option value="">Select a volume…</option>
              {(volPage?.data ?? []).map((v) => (
                <option key={v.volume_name} value={v.volume_name}>{v.volume_name}</option>
              ))}
            </select>
          </div>

          {selectedVolume && growthLoading && <p className="text-gray-500 text-sm">Loading volume trend…</p>}

          {selectedVolume && !growthLoading && (
            <>
              {g ? (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                  <StatCard label="Window Start" value={tb(g.start_used_tb)} sub={trendDate(g.start_date)} />
                  <StatCard label="Current Used" value={tb(g.current_used_tb)} />
                  <div className="card">
                    <p className="text-xs text-gray-500">Change</p>
                    <p className={`text-2xl font-semibold mt-1 flex items-center gap-1 ${up ? 'text-emerald-400' : 'text-red-400'}`}>
                      {up ? <TrendingUp size={18} /> : <TrendingDown size={18} />}
                      {up ? '+' : ''}{tb(g.growth_tb)}
                    </p>
                  </div>
                  <StatCard
                    label="Change %"
                    value={g.growth_pct != null ? `${g.growth_pct > 0 ? '+' : ''}${g.growth_pct}%` : '—'}
                  />
                </div>
              ) : (
                <p className="text-gray-600 text-sm mb-3">
                  Not enough history for this volume yet — need at least two snapshots in the window.
                </p>
              )}

              {trend.length > 0 && (
                <ResponsiveContainer width="100%" height={240}>
                  <LineChart data={trend}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis dataKey="date" tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={trendDate} />
                    <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={(v: number) => `${v} TB`} width={64} />
                    <Tooltip {...tooltipStyle} labelFormatter={trendDate} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Line type="monotone" dataKey="size_tb" name="Provisioned TB" stroke="#6b7280" strokeDasharray="4 3" dot={false} strokeWidth={1.5} />
                    <Line type="monotone" dataKey="used_tb" name="Used TB" stroke="#3b82f6" dot={false} strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </>
          )}
        </div>
      )}

      {/* Top volume movers (fleet or scoped to array) */}
      <h4 className="text-xs font-semibold text-gray-400 mb-2">
        Top Volume Growers & Shrinkers {selectedArray ? `— ${selectedArray}` : '(Fleet-wide)'}
      </h4>

      {moversLoading && <p className="text-gray-500 text-sm">Loading…</p>}

      {!moversLoading && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 border-b border-gray-800">
                <th className="py-2 pr-4">Volume</th>
                {!selectedArray && <th className="py-2 pr-4">Array</th>}
                <th className="py-2 pr-4">Vendor</th>
                <th className="py-2 pr-4 text-right">Start Used</th>
                <th className="py-2 pr-4 text-right">Current Used</th>
                <th className="py-2 pr-4 text-right">Change</th>
                <th className="py-2 pr-4 text-right">Change %</th>
              </tr>
            </thead>
            <tbody>
              {moverRows.map((r) => {
                const rup = r.delta_tb >= 0
                return (
                  <tr key={`${r.array_name}|${r.volume_name}`} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="py-2 pr-4 text-gray-200 font-medium max-w-xs truncate" title={r.volume_name}>{r.volume_name}</td>
                    {!selectedArray && <td className="py-2 pr-4 text-gray-400">{r.array_name}</td>}
                    <td className="py-2 pr-4 text-gray-400 capitalize">{r.vendor}</td>
                    <td className="py-2 pr-4 text-right text-gray-300">{tb(r.start_used_tb)}</td>
                    <td className="py-2 pr-4 text-right text-gray-300">{tb(r.current_used_tb)}</td>
                    <td className={`py-2 pr-4 text-right font-medium ${rup ? 'text-emerald-400' : 'text-red-400'}`}>
                      <span className="inline-flex items-center gap-1 justify-end">
                        {rup ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                        {rup ? '+' : ''}{tb(r.delta_tb)}
                      </span>
                    </td>
                    <td className={`py-2 pr-4 text-right ${rup ? 'text-emerald-400' : 'text-red-400'}`}>
                      {r.delta_pct != null ? `${r.delta_pct > 0 ? '+' : ''}${r.delta_pct}%` : '—'}
                    </td>
                  </tr>
                )
              })}
              {moverRows.length === 0 && (
                <tr>
                  <td colSpan={selectedArray ? 6 : 7} className="py-6 text-center text-gray-600">
                    No volume history in this window yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Capacity forecast ("how full will X be by <date>") ────────────────────────

function ForecastPanel({ arrays }: { arrays: ArraySummary[] }) {
  const [selected, setSelected] = useState('')   // '' = whole fleet
  // Default target ~90 days out.
  const defaultTarget = new Date(Date.now() + 90 * 86_400_000).toISOString().slice(0, 10)
  const [target, setTarget] = useState(defaultTarget)

  const { data: fc, isLoading, isError } = useQuery<CapacityForecast>({
    queryKey: ['forecast', selected, target],
    queryFn: () => arraysApi.forecast(selected || undefined, target || undefined, 90),
    refetchInterval: 300_000,
  })

  const projPct = fc?.projected_pct ?? null
  const curPct = fc?.current_pct ?? null
  const overfull = projPct != null && projPct >= 100
  const growing = fc?.trend === 'growing'
  const changePos = (fc?.projected_change_tb ?? 0) >= 0

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-brand-400" />
          <h3 className="text-sm font-semibold text-gray-300">Capacity Forecast</h3>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-brand-500"
          >
            <option value="">Whole fleet</option>
            {[...arrays]
              .sort((a, b) => a.array_name.localeCompare(b.array_name))
              .map((a) => <option key={a.array_name} value={a.array_name}>{a.array_name}</option>)}
          </select>
          <input
            type="date"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-brand-500"
          />
        </div>
      </div>

      {isLoading && <p className="text-gray-500 text-sm py-4">Computing forecast…</p>}
      {isError && <p className="text-gray-500 text-sm py-4">Forecast unavailable right now.</p>}

      {fc && !isLoading && (
        fc.error ? (
          <p className="text-gray-500 text-sm py-4">{fc.error}</p>
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="card">
                <p className="text-xs text-gray-500">Current Used</p>
                <p className="text-2xl font-semibold text-white mt-1">{tb(fc.current_used_tb)}</p>
                <p className="text-xs text-gray-500 mt-0.5">{curPct != null ? `${curPct}% of ${tb(fc.usable_tb)}` : '—'}</p>
              </div>
              <div className="card">
                <p className="text-xs text-gray-500">Projected by {trendDate(target)}</p>
                <p className={`text-2xl font-semibold mt-1 ${overfull ? 'text-red-400' : 'text-white'}`}>
                  {fc.projected_used_tb != null ? tb(fc.projected_used_tb) : '—'}
                </p>
                <p className={`text-xs mt-0.5 ${overfull ? 'text-red-400' : 'text-gray-500'}`}>
                  {projPct != null ? (overfull ? `≥100% — full before then` : `${projPct}% projected`) : '—'}
                </p>
              </div>
              <div className="card">
                <p className="text-xs text-gray-500">Change</p>
                <p className={`text-2xl font-semibold mt-1 flex items-center gap-1 ${changePos ? 'text-emerald-400' : 'text-red-400'}`}>
                  {changePos ? <TrendingUp size={18} /> : <TrendingDown size={18} />}
                  {fc.projected_change_tb != null ? `${changePos ? '+' : ''}${tb(fc.projected_change_tb)}` : '—'}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">{fc.rate_tb_per_day >= 0 ? '+' : ''}{tb(Math.abs(fc.rate_tb_per_day))}/day</p>
              </div>
              <div className="card">
                <p className="text-xs text-gray-500">Projected Full</p>
                <p className={`text-2xl font-semibold mt-1 ${fc.days_to_full != null && fc.days_to_full < 90 ? 'text-red-400' : 'text-white'}`}>
                  {fc.days_to_full != null ? `~${fc.days_to_full}d` : 'N/A'}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  {fc.projected_full_date ? `by ${trendDate(fc.projected_full_date)}` : growing ? 'at current rate' : 'not filling'}
                </p>
              </div>
            </div>

            {/* current vs projected against usable */}
            {curPct != null && (
              <div className="mt-4">
                <div className="relative h-2.5 bg-gray-800 rounded-full overflow-hidden">
                  {projPct != null && (
                    <div
                      className={`absolute inset-y-0 left-0 ${overfull ? 'bg-red-500/40' : 'bg-brand-500/30'}`}
                      style={{ width: `${Math.min(projPct, 100)}%` }}
                    />
                  )}
                  <div
                    className={`absolute inset-y-0 left-0 ${curPct >= 90 ? 'bg-red-500' : curPct >= 75 ? 'bg-amber-500' : 'bg-brand-500'}`}
                    style={{ width: `${Math.min(curPct, 100)}%` }}
                  />
                </div>
                <div className="flex justify-between text-[10px] text-gray-600 mt-1">
                  <span>now {curPct}%</span>
                  <span>{projPct != null ? `projected ${overfull ? '≥100' : projPct}%` : ''}</span>
                </div>
              </div>
            )}

            <p className="text-xs text-gray-600 mt-3">
              Basis: {fc.window_days}-day trend ({fc.data_points} daily points){fc.caveat ? ` · ${fc.caveat}` : ''}.
              {' '}Ask the <a href="/chat" className="text-brand-400 hover:text-brand-300">Storage AI</a> for details.
            </p>
          </>
        )
      )}
    </div>
  )
}

export default function Capacity() {

  const [view, setView] = useState<'vendor' | 'cloud'>('vendor')
  const [exporting, setExporting] = useState(false)

  const { data: arrayList = [] } = useQuery<ArraySummary[]>({
    queryKey: ['arrays'],
    queryFn: () => arraysApi.list(),
  })


  const handleExport = async () => {
    setExporting(true)
    try {
      await arraysApi.exportCapacityXlsx()
    } catch (e) {
      console.error('Capacity export failed', e)
    } finally {
      setExporting(false)
    }
  }


  const { data, isLoading } = useQuery<CapacityBreakdown>({
    queryKey: ['capacity-breakdown'],
    queryFn: () => arraysApi.capacityBreakdown(),
    refetchInterval: 300_000,
  })

  const fleet = data?.fleet
  const chartRows = (view === 'vendor' ? data?.by_vendor : data?.by_cloud) ?? []
  const chartData = chartRows.map((r) => ({
    name: view === 'vendor' ? (r as VendorBucket).vendor : (r as CloudBucket).cloud,
    Used: r.used_tb,
    Free: r.free_tb,
  }))

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-white">Capacity</h2>
        <div className="flex items-center gap-3">
          {fleet && (
            <span className="text-xs text-gray-500">
              {fleet.arrays} arrays · {tb(fleet.usable_tb)} usable
            </span>
          )}
          <button
            onClick={handleExport}
            disabled={exporting}
            className="flex items-center gap-2 text-xs px-3 py-1.5 rounded border border-gray-700 text-gray-300 hover:border-brand-500 hover:text-brand-400 transition-colors disabled:opacity-50"
            title="Download a multi-sheet Excel capacity report"
          >
            <Download size={14} />
            {exporting ? 'Exporting…' : 'Export Excel'}
          </button>
        </div>
      </div>


      {isLoading && <p className="text-gray-500 text-sm">Loading capacity breakdown…</p>}

      {fleet && (
        <>
          {/* Fleet summary */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard label="Usable (Allocated)" value={tb(fleet.usable_tb)} sub={`${fleet.arrays} arrays`} />
            <StatCard label="Used" value={tb(fleet.used_tb)} />
            <StatCard label="Free" value={tb(fleet.free_tb)} />
            <StatCard label="Utilization" value={`${fleet.utilization_pct.toFixed(1)}%`}
              sub={fleet.utilization_pct >= 85 ? 'running high' : undefined} />
          </div>

          {/* ── Capacity Trends ─────────────────────────────────────────── */}
          <FleetGrowthSummary />
          <FleetTrendChart />

          <ForecastPanel arrays={arrayList} />

          <TopMoversTable />
          <ArrayGrowthDetail arrays={arrayList} />

          {/* ── Per-volume consumption growth (volumes_history) ──────────── */}
          <VolumeGrowthSection arrays={arrayList} />


          {/* View toggle + stacked bar chart */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-gray-300">
                Used vs Free by {view === 'vendor' ? 'Platform' : 'Cloud'}
              </h3>

              <div className="flex gap-1">
                {(['vendor', 'cloud'] as const).map((v) => (
                  <button
                    key={v}
                    onClick={() => setView(v)}
                    className={`text-xs px-3 py-1.5 rounded border transition-colors capitalize ${
                      view === v
                        ? 'bg-brand-600/20 text-brand-400 border-brand-500/30'
                        : 'text-gray-400 border-gray-700 hover:border-gray-500'
                    }`}
                  >
                    {v === 'vendor' ? 'Platform' : 'Cloud'}
                  </button>
                ))}
              </div>
            </div>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="name" tick={{ fill: '#9ca3af', fontSize: 11 }} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={(v) => `${v} TB`} width={60} />
                <Tooltip {...tooltipStyle} formatter={(v: number) => tb(v)} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="Used" stackId="cap" fill="#3b82f6" />
                <Bar dataKey="Free" stackId="cap" fill="#374151" />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Breakdown tables */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <BreakdownTable<VendorBucket>
              title="By Platform"
              label="Vendor"
              rows={data!.by_vendor}
              keyField={(r) => r.vendor}
            />
            <BreakdownTable<CloudBucket>
              title="By Cloud / Group"
              label="Cloud"
              rows={data!.by_cloud}
              keyField={(r) => r.cloud}
            />
          </div>

          {/* Vendor × Cloud pivot */}
          <div className="card">
            <h3 className="text-sm font-semibold text-gray-300 mb-3">Platform × Cloud</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-gray-500 border-b border-gray-800">
                    <th className="py-2 pr-4">Vendor</th>
                    <th className="py-2 pr-4">Cloud</th>
                    <th className="py-2 pr-4 text-right">Arrays</th>
                    <th className="py-2 pr-4 text-right">Usable</th>
                    <th className="py-2 pr-4 text-right">Used</th>
                    <th className="py-2 pr-4 text-right">Free</th>
                    <th className="py-2 pr-4 w-40">Utilization</th>
                  </tr>
                </thead>
                <tbody>
                  {data!.by_vendor_cloud.map((r: VendorCloudBucket) => (
                    <tr key={`${r.vendor}|${r.cloud}`} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                      <td className="py-2 pr-4 text-gray-200 font-medium capitalize">{r.vendor}</td>
                      <td className="py-2 pr-4 text-gray-400">{r.cloud}</td>
                      <td className="py-2 pr-4 text-right text-gray-400">{r.arrays}</td>
                      <td className="py-2 pr-4 text-right text-gray-300">{tb(r.usable_tb)}</td>
                      <td className="py-2 pr-4 text-right text-gray-300">{tb(r.used_tb)}</td>
                      <td className="py-2 pr-4 text-right text-gray-300">{tb(r.free_tb)}</td>
                      <td className="py-2 pr-4"><UtilBar pct={r.utilization_pct} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
