import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { arraysApi } from '@/api/arrays'
import { formatTB as tb } from '@/utils/formatters'
import type { CapacityHistoryResponse, CapacityHistoryPoint } from '@/api/types'

// Match the dashboard's existing recharts palette (the brand here). Used vs Free
// are components of the SAME measure (TB), so a stacked area is one measure split
// into parts — not a dual-axis chart.
const C_USED = '#2A78D6'   // brand blue
const C_FREE = '#4b5563'   // muted gray (headroom)
const RANGES = [
  { d: 30, label: '30d' }, { d: 90, label: '90d' }, { d: 180, label: '180d' }, { d: 365, label: '1y' },
]

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card py-3">
      <div className="text-2xl font-semibold text-white leading-tight">{value}</div>
      <div className="text-xs text-gray-500 mt-0.5">{label}</div>
      {sub && <div className="text-[11px] text-gray-600">{sub}</div>}
    </div>
  )
}

export default function CapacityExplorer() {
  const [days, setDays] = useState(90)
  const [vendor, setVendor] = useState('')
  const [model, setModel] = useState('')
  const [groups, setGroups] = useState<Set<string>>(new Set())   // empty = all
  const [groupSearch, setGroupSearch] = useState('')

  const { data, isLoading, isError } = useQuery<CapacityHistoryResponse>({
    queryKey: ['capacity-history', days],
    queryFn: () => arraysApi.capacityHistory(days),
  })

  const arrays = data?.arrays ?? []
  const points = data?.points ?? []

  const vendors = useMemo(() => [...new Set(arrays.map(a => a.vendor))].sort(), [arrays])
  const models = useMemo(() => [...new Set(arrays.map(a => a.model).filter(Boolean))].sort(), [arrays])
  const allGroups = useMemo(() => [...new Set(arrays.map(a => a.group))].sort(), [arrays])
  const shownGroups = useMemo(
    () => allGroups.filter(g => g.toLowerCase().includes(groupSearch.toLowerCase())),
    [allGroups, groupSearch],
  )

  // arrays surviving the slicers
  const selected = useMemo(() => {
    const names = new Set<string>()
    for (const a of arrays) {
      if (vendor && a.vendor !== vendor) continue
      if (model && a.model !== model) continue
      if (groups.size && !groups.has(a.group)) continue
      names.add(a.array_name)
    }
    return names
  }, [arrays, vendor, model, groups])

  // aggregate the filtered subset: per-day sum(used) / sum(total); last point per array
  const { series, lastByArray } = useMemo(() => {
    const byDay = new Map<string, { used: number; total: number }>()
    const last = new Map<string, CapacityHistoryPoint>()
    for (const p of points) {
      if (!selected.has(p.a)) continue
      const day = byDay.get(p.d) ?? { used: 0, total: 0 }
      day.used += p.used_tb; day.total += p.total_tb
      byDay.set(p.d, day)
      last.set(p.a, p)   // points are day-ascending, so last wins
    }
    const series = [...byDay.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([d, v]) => ({ d, used: Math.round(v.used), free: Math.round(Math.max(v.total - v.used, 0)), usable: Math.round(v.total) }))
    return { series, lastByArray: last }
  }, [points, selected])

  // KPIs from the latest reading of each selected array
  const kpi = useMemo(() => {
    const rows = [...lastByArray.values()]
    const n = rows.length
    const totalUsed = rows.reduce((s, r) => s + r.used_tb, 0)
    const totalUsable = rows.reduce((s, r) => s + r.total_tb, 0)
    const avgUsedPct = n ? rows.reduce((s, r) => s + r.used_pct, 0) / n : 0
    return { n, totalUsed, totalUsable, avgUsedPct, avgFreePct: 100 - avgUsedPct }
  }, [lastByArray])

  // per-array list (latest), busiest first
  const arrayRows = useMemo(() => {
    return [...lastByArray.entries()]
      .map(([a, p]) => ({ a, used_tb: p.used_tb, total_tb: p.total_tb, used_pct: p.used_pct }))
      .sort((x, y) => y.used_tb - x.used_tb)
  }, [lastByArray])

  function toggleGroup(g: string) {
    setGroups(prev => { const n = new Set(prev); n.has(g) ? n.delete(g) : n.add(g); return n })
  }
  const allSelected = groups.size === 0 || groups.size === allGroups.length
  function toggleAll() {
    setGroups(allSelected ? new Set(allGroups) : new Set())   // toggle between all-explicit and none(=all)
  }
  function reset() { setVendor(''); setModel(''); setGroups(new Set()); setGroupSearch('') }

  const activeChips = [
    vendor && { k: 'Vendor', v: vendor, clear: () => setVendor('') },
    model && { k: 'Model', v: model, clear: () => setModel('') },
    groups.size > 0 && groups.size < allGroups.length && { k: 'Groups', v: `${groups.size} selected`, clear: () => setGroups(new Set()) },
  ].filter(Boolean) as { k: string; v: string; clear: () => void }[]

  const sel = 'w-full bg-gray-800 border border-gray-700 text-xs text-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:border-brand-500'

  return (
    <div className="flex gap-4">
      {/* ── Slicer rail ─────────────────────────────────────────── */}
      <aside className="w-52 shrink-0 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Slicers</h3>
          <button onClick={reset} className="text-[11px] text-gray-500 hover:text-brand-400">Reset</button>
        </div>

        <div>
          <label className="text-[11px] text-gray-500">Range</label>
          <div className="flex gap-1 mt-1">
            {RANGES.map(r => (
              <button key={r.d} onClick={() => setDays(r.d)}
                className={`text-[11px] px-2 py-1 rounded border ${days === r.d ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'text-gray-400 border-gray-700 hover:border-gray-500'}`}>
                {r.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="text-[11px] text-gray-500">Vendor</label>
          <select value={vendor} onChange={e => setVendor(e.target.value)} className={`${sel} mt-1`}>
            <option value="">All</option>
            {vendors.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>

        <div>
          <label className="text-[11px] text-gray-500">Model</label>
          <select value={model} onChange={e => setModel(e.target.value)} className={`${sel} mt-1`}>
            <option value="">All</option>
            {models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <label className="text-[11px] text-gray-500">Site / Group</label>
            <span className="text-[10px] text-gray-600">{groups.size === 0 ? 'All' : `${groups.size}`}</span>
          </div>
          <div className="relative mt-1">
            <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-600" />
            <input value={groupSearch} onChange={e => setGroupSearch(e.target.value)} placeholder="search…"
              className={`${sel} pl-6`} />
          </div>
          <label className="flex items-center gap-2 text-[11px] text-gray-400 mt-1.5 cursor-pointer">
            <input type="checkbox" checked={allSelected} onChange={toggleAll} className="accent-brand-500" />
            Select All
          </label>
          <div className="mt-1 max-h-56 overflow-y-auto space-y-0.5 pr-1">
            {shownGroups.map(g => (
              <label key={g} className="flex items-center gap-2 text-[11px] text-gray-300 cursor-pointer hover:text-white">
                <input type="checkbox" checked={groups.size === 0 ? false : groups.has(g)} onChange={() => toggleGroup(g)} className="accent-brand-500" />
                <span className="truncate" title={g}>{g}</span>
              </label>
            ))}
          </div>
        </div>
      </aside>

      {/* ── Board ───────────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 space-y-4">
        {activeChips.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {activeChips.map(c => (
              <button key={c.k} onClick={c.clear} className="text-[11px] bg-gray-800 border border-gray-700 rounded-full px-2 py-0.5 text-gray-300 hover:border-red-500/40">
                {c.k}: {c.v} ✕
              </button>
            ))}
          </div>
        )}

        {/* KPI row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <KpiCard label="Arrays" value={String(kpi.n)} />
          <KpiCard label="Used" value={tb(kpi.totalUsed)} sub={`of ${tb(kpi.totalUsable)} usable`} />
          <KpiCard label="Avg Used %" value={`${kpi.avgUsedPct.toFixed(1)}%`} />
          <KpiCard label="Avg Free %" value={`${kpi.avgFreePct.toFixed(1)}%`} />
        </div>

        {/* Capacity trend */}
        <div className="card">
          <h3 className="text-sm font-semibold text-gray-300 mb-2">Capacity Trend <span className="text-gray-600 text-xs">— Used vs Free (TB), {kpi.n} arrays</span></h3>
          {isError ? (
            <p className="text-red-400 text-sm py-8 text-center">Failed to load capacity history.</p>
          ) : isLoading ? (
            <p className="text-gray-500 text-sm py-8 text-center">Loading…</p>
          ) : series.length === 0 ? (
            <p className="text-gray-500 text-sm py-8 text-center">No data for this selection.</p>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={series} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="d" tick={{ fill: '#9ca3af', fontSize: 11 }} minTickGap={40} />
                <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} width={48} />
                <Tooltip contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8 }}
                  itemStyle={{ color: '#e5e7eb' }} labelStyle={{ color: '#e5e7eb' }}
                  formatter={(v: number, n: string) => [`${v.toLocaleString()} TB`, n]} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Area type="monotone" dataKey="used" name="Used" stackId="1" stroke={C_USED} fill={C_USED} fillOpacity={0.85} strokeWidth={2} />
                <Area type="monotone" dataKey="free" name="Free" stackId="1" stroke={C_FREE} fill={C_FREE} fillOpacity={0.5} strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Per-array list */}
        <div className="card">
          <h3 className="text-sm font-semibold text-gray-300 mb-2">By Array <span className="text-gray-600 text-xs">— latest, busiest first</span></h3>
          <div className="overflow-x-auto max-h-96 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="text-gray-500 sticky top-0 bg-gray-900/80">
                <tr className="text-left"><th className="py-1 pr-3">Array</th><th className="pr-3 text-right">Used TB</th><th className="pr-3 text-right">Usable TB</th><th className="text-right">Used %</th></tr>
              </thead>
              <tbody>
                {arrayRows.map(r => (
                  <tr key={r.a} className="border-t border-gray-800">
                    <td className="py-1.5 pr-3 font-mono text-gray-200">{r.a}</td>
                    <td className="pr-3 text-right text-gray-300">{tb(r.used_tb)}</td>
                    <td className="pr-3 text-right text-gray-400">{tb(r.total_tb)}</td>
                    <td className="text-right">
                      <span className={r.used_pct >= 85 ? 'text-red-400' : r.used_pct >= 70 ? 'text-amber-400' : 'text-gray-300'}>{r.used_pct.toFixed(1)}%</span>
                    </td>
                  </tr>
                ))}
                {arrayRows.length === 0 && <tr><td colSpan={4} className="py-4 text-center text-gray-600">No arrays match.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
