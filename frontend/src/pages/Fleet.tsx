import { useMemo, useRef, useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { arraysApi } from '@/api/arrays'
import type { FleetArray } from '@/api/types'

const TiB = 1099511627776
const PiB = TiB * 1024
const CSPS = ['AWS', 'Azure', 'GCP', 'On-Prem'] as const
// Validated dark categorical hues (data-viz reference palette, dark surface).
const CSP_COLOR: Record<string, string> = {
  AWS: '#3987e5', Azure: '#d95926', GCP: '#199e70', 'On-Prem': '#c98500',
}
const TECH_ORDER = ['Block', 'File', 'Object', 'Unknown']

function fmtCap(b: number): string {
  if (b >= PiB) return (b / PiB).toFixed(2) + ' PiB'
  if (b >= TiB) return (b / TiB).toFixed(1) + ' TiB'
  return (b / (TiB / 1024)).toFixed(0) + ' GiB'
}
const fmtNum = (n: number) => (n >= 1000 ? n.toLocaleString() : String(n))
const usedColor = (p: number) => (p >= 90 ? '#ef4444' : p >= 75 ? '#f59e0b' : '#3b82f6')

// ---- squarified treemap (Bruls/Huizing/van Wijk) ----
type Cell = { node: DCNode; x: number; y: number; w: number; h: number }
type DCNode = { dc: string; csp: string; value: number; used: number; n: number }
function squarify(nodes: DCNode[], W: number, H: number): Cell[] {
  const list = nodes.slice().sort((a, b) => b.value - a.value)
  const total = list.reduce((s, n) => s + n.value, 0)
  if (!total || W <= 0 || H <= 0) return []
  const scale = (W * H) / total
  const items = list.map((n) => ({ node: n, area: n.value * scale }))
  const result: Cell[] = []
  let cx = 0, cy = 0, cw = W, ch = H
  let row: { node: DCNode; area: number }[] = []
  let i = 0
  const worst = (rw: typeof row, side: number) => {
    const s = rw.reduce((a, n) => a + n.area, 0)
    if (s <= 0) return Infinity
    const mx = Math.max(...rw.map((n) => n.area)), mn = Math.min(...rw.map((n) => n.area))
    const s2 = s * s, d2 = side * side
    return Math.max((d2 * mx) / s2, s2 / (d2 * mn))
  }
  const flush = (rw: typeof row) => {
    const s = rw.reduce((a, n) => a + n.area, 0)
    if (cw >= ch) {
      const colW = s / ch; let off = 0
      rw.forEach((n) => { const hh = (n.area / s) * ch; result.push({ node: n.node, x: cx, y: cy + off, w: colW, h: hh }); off += hh })
      cx += colW; cw -= colW
    } else {
      const rowH = s / cw; let off = 0
      rw.forEach((n) => { const ww = (n.area / s) * cw; result.push({ node: n.node, x: cx + off, y: cy, w: ww, h: rowH }); off += ww })
      cy += rowH; ch -= rowH
    }
  }
  while (i < items.length) {
    const side = Math.min(cw, ch)
    if (row.length === 0) { row.push(items[i++]); continue }
    if (worst(row.concat([items[i]]), side) <= worst(row, side)) row.push(items[i++])
    else { flush(row); row = [] }
  }
  if (row.length) flush(row)
  return result
}

type Tip = { html: string; x: number; y: number } | null

export default function Fleet() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['fleet-overview'],
    queryFn: () => arraysApi.fleetOverview(),
    refetchInterval: 60_000,
  })
  const all: FleetArray[] = data?.arrays ?? []

  const [csp, setCsp] = useState<Set<string>>(new Set())
  const [dc, setDc] = useState<Set<string>>(new Set())
  const [vendor, setVendor] = useState<Set<string>>(new Set())
  const [tech, setTech] = useState<Set<string>>(new Set())
  const [onlyAlerts, setOnlyAlerts] = useState(false)
  const [sortKey, setSortKey] = useState<keyof FleetArray>('cap_total')
  const [sortDir, setSortDir] = useState(-1)
  const [search, setSearch] = useState('')
  const [detail, setDetail] = useState<FleetArray | null>(null)
  const [tip, setTip] = useState<Tip>(null)

  const toggle = (s: Set<string>, setter: (v: Set<string>) => void, v: string) => {
    const n = new Set(s); n.has(v) ? n.delete(v) : n.add(v); setter(n)
  }
  const clearAll = () => { setCsp(new Set()); setDc(new Set()); setVendor(new Set()); setTech(new Set()); setOnlyAlerts(false) }

  const filtered = useMemo(() => all.filter((a) => {
    if (csp.size && !csp.has(a.csp)) return false
    if (dc.size && !dc.has(a.dc)) return false
    if (vendor.size && !vendor.has(a.vendor)) return false
    if (tech.size && !tech.has(a.tech)) return false
    if (onlyAlerts && a.alerts <= 0) return false
    return true
  }), [all, csp, dc, vendor, tech, onlyAlerts])

  // KPIs
  const k = useMemo(() => {
    const cap = filtered.reduce((s, a) => s + a.cap_total, 0)
    const used = filtered.reduce((s, a) => s + a.cap_used, 0)
    const drs = filtered.filter((a) => a.dr != null).map((a) => a.dr as number)
    return {
      arrays: filtered.length,
      cap, used, usedPct: cap ? (used / cap) * 100 : 0,
      dr: drs.length ? drs.reduce((s, x) => s + x, 0) / drs.length : 0, drN: drs.length,
      hosts: filtered.reduce((s, a) => s + a.hosts, 0),
      vols: filtered.reduce((s, a) => s + a.vols, 0),
      alerts: filtered.reduce((s, a) => s + a.alerts, 0),
    }
  }, [filtered])

  // treemap width (responsive)
  const treeRef = useRef<HTMLDivElement>(null)
  const [treeW, setTreeW] = useState(900)
  useEffect(() => {
    if (!treeRef.current) return
    const ro = new ResizeObserver((e) => setTreeW(e[0].contentRect.width))
    ro.observe(treeRef.current)
    return () => ro.disconnect()
  }, [])
  const treeH = 340
  const cells = useMemo(() => {
    const byDC: Record<string, DCNode> = {}
    for (const a of filtered) {
      const n = (byDC[a.dc] ??= { dc: a.dc, csp: a.csp, value: 0, used: 0, n: 0 })
      n.value += a.cap_total; n.used += a.cap_used; n.n++
    }
    return squarify(Object.values(byDC).filter((n) => n.value > 0), treeW, treeH)
  }, [filtered, treeW])

  const agg = (dim: keyof FleetArray) => {
    const m: Record<string, number> = {}
    for (const a of all) { const v = String(a[dim]); m[v] = (m[v] ?? 0) + 1 }
    return m
  }

  const table = useMemo(() => {
    let f = filtered
    if (search) { const q = search.toLowerCase(); f = f.filter((a) => (a.name + ' ' + (a.model || '')).toLowerCase().includes(q)) }
    const strKeys = new Set(['name', 'vendor', 'dc', 'csp', 'tech', 'model'])
    return f.slice().sort((a, b) => {
      let x: any = a[sortKey], y: any = b[sortKey]
      if (strKeys.has(sortKey as string)) return String(x || '').localeCompare(String(y || '')) * sortDir
      return (((x ?? -1) as number) - ((y ?? -1) as number)) * sortDir
    })
  }, [filtered, search, sortKey, sortDir])
  const setSort = (key: keyof FleetArray) => {
    if (sortKey === key) setSortDir((d) => -d)
    else { setSortKey(key); setSortDir(['name', 'vendor', 'dc', 'csp', 'tech', 'model'].includes(key as string) ? 1 : -1) }
  }

  const activeCount = csp.size + dc.size + vendor.size + tech.size + (onlyAlerts ? 1 : 0)

  if (isLoading) return <div className="text-gray-500 text-sm p-4">Loading fleet…</div>
  if (isError) return <div className="text-red-400 text-sm p-4">Failed to load fleet data.</div>

  const Slicer = ({ label, dim, set, setter, order, colored }: {
    label: string; dim: keyof FleetArray; set: Set<string>; setter: (v: Set<string>) => void; order?: string[]; colored?: boolean
  }) => {
    const counts = agg(dim)
    const keys = (order || Object.keys(counts).sort()).filter((x) => counts[x] != null)
    return (
      <div className="flex flex-col gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-gray-600 font-mono">{label}</span>
        <div className="flex flex-wrap gap-1.5">
          {keys.map((key) => {
            const on = set.has(key)
            return (
              <button key={key} onClick={() => toggle(set, setter, key)}
                className={`text-xs px-2.5 py-1 rounded-full border flex items-center gap-1.5 transition-colors ${
                  on ? 'text-white border-transparent font-semibold' : 'text-gray-400 border-gray-700 bg-gray-800/60 hover:border-brand-500 hover:text-gray-200'}`}
                style={on ? { background: colored ? CSP_COLOR[key] : '#2563eb' } : undefined}>
                {colored && <span className="w-2 h-2 rounded-sm" style={{ background: CSP_COLOR[key] }} />}
                {key}<span className="opacity-70 text-[10px] tabular-nums">{counts[key]}</span>
              </button>
            )
          })}
        </div>
      </div>
    )
  }

  const kpis = [
    { l: 'Arrays', v: fmtNum(k.arrays), u: '', s: `of ${all.length} total`, onClick: clearAll },
    { l: 'Total Capacity', v: fmtCap(k.cap).split(' ')[0], u: fmtCap(k.cap).split(' ')[1], s: 'usable' },
    { l: 'Used Capacity', v: fmtCap(k.used).split(' ')[0], u: fmtCap(k.used).split(' ')[1], s: `${k.usedPct.toFixed(0)}% of usable`, bar: k.usedPct },
    { l: 'Avg Data Reduction', v: k.dr.toFixed(2), u: ':1', s: `${k.drN} reporting` },
    { l: 'Hosts', v: fmtNum(k.hosts), u: '', s: 'attached' },
    { l: 'Volumes', v: fmtNum(k.vols), u: '', s: 'provisioned' },
    { l: 'Active Alerts', v: fmtNum(k.alerts), u: '', s: onlyAlerts ? 'filtering ✓' : 'click to filter', hot: k.alerts > 0, active: onlyAlerts, onClick: () => setOnlyAlerts((v) => !v) },
  ]

  const BarChart = ({ dim, colored }: { dim: keyof FleetArray; colored?: boolean }) => {
    const m: Record<string, { k: string; cap: number; used: number; n: number }> = {}
    for (const a of filtered) { const key = String(a[dim]); const g = (m[key] ??= { k: key, cap: 0, used: 0, n: 0 }); g.cap += a.cap_total; g.used += a.cap_used; g.n++ }
    const rows = Object.values(m).sort((a, b) => b.cap - a.cap)
    const max = Math.max(...rows.map((r) => r.cap), 1)
    const selSet = dim === 'csp' ? csp : dim === 'vendor' ? vendor : tech
    const setter = dim === 'csp' ? setCsp : dim === 'vendor' ? setVendor : setTech
    return (
      <div className="flex flex-col gap-2.5 mt-2">
        {rows.map((r) => {
          const col = colored ? CSP_COLOR[r.k] : '#3b82f6'
          const pct = r.cap ? (r.used / r.cap) * 100 : 0
          const sel = selSet.has(r.k), dim2 = selSet.size > 0 && !sel
          return (
            <div key={r.k} className={`cursor-pointer ${dim2 ? 'opacity-40' : ''}`} onClick={() => toggle(selSet, setter, r.k)}
              onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, html: `<b>${r.k}</b><br/>Usable ${fmtCap(r.cap)}<br/>Used ${fmtCap(r.used)} (${pct.toFixed(0)}%)<br/>${r.n} arrays` })}
              onMouseLeave={() => setTip(null)}>
              <div className="flex justify-between text-xs mb-1">
                <span className={`flex items-center gap-1.5 ${sel ? 'text-white font-semibold' : 'text-gray-400'}`}>
                  {colored && <span className="w-2 h-2 rounded-sm" style={{ background: col }} />}{r.k}
                </span>
                <span className="text-gray-500 tabular-nums">{fmtCap(r.cap)} · {pct.toFixed(0)}%</span>
              </div>
              <div className="h-4 rounded bg-gray-800 relative overflow-hidden">
                <span className="absolute inset-y-0 left-0 rounded opacity-30" style={{ width: `${(r.cap / max) * 100}%`, background: col }} />
                <span className="absolute inset-y-0 left-0 rounded" style={{ width: `${(r.used / max) * 100}%`, background: col }} />
              </div>
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-semibold text-white">Fleet Overview</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {filtered.length === all.length ? 'all arrays' : `${filtered.length} of ${all.length} arrays`} · slice by cloud, datacenter, vendor, technology
          </p>
        </div>
        <div className="flex gap-3 flex-wrap text-xs text-gray-400">
          {CSPS.map((c) => (
            <span key={c} className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: CSP_COLOR[c] }} />{c}</span>
          ))}
        </div>
      </div>

      {/* slicers */}
      <div className="card">
        <div className="flex gap-x-6 gap-y-3 flex-wrap items-start">
          <Slicer label="Cloud" dim="csp" set={csp} setter={setCsp} order={[...CSPS]} colored />
          <Slicer label="Datacenter" dim="dc" set={dc} setter={setDc} />
          <Slicer label="Vendor" dim="vendor" set={vendor} setter={setVendor} />
          <Slicer label="Technology" dim="tech" set={tech} setter={setTech} order={TECH_ORDER} />
          <div className="ml-auto self-center flex items-center gap-3">
            <span className="text-xs text-gray-500">{activeCount ? `${activeCount} filter${activeCount > 1 ? 's' : ''} active` : 'no filters'}</span>
            <button onClick={clearAll} className="text-xs text-brand-400 border border-gray-700 rounded px-2 py-1 hover:border-brand-500">Clear all</button>
          </div>
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
        {kpis.map((t) => (
          <div key={t.l} onClick={t.onClick}
            className={`card py-3 ${t.onClick ? 'cursor-pointer hover:-translate-y-0.5 transition-transform' : ''} ${t.active ? 'ring-1 ring-brand-500 border-brand-500' : ''}`}>
            <div className="text-[10px] uppercase tracking-wide text-gray-600 font-mono">{t.l}</div>
            <div className={`text-2xl font-semibold leading-tight mt-0.5 tabular-nums ${t.hot ? 'text-red-400' : 'text-white'}`}>
              {t.v}<span className="text-sm text-gray-500 font-medium"> {t.u}</span>
            </div>
            <div className="text-[11px] text-gray-500">{t.s}</div>
            {t.bar != null && (
              <div className="h-1 rounded bg-gray-800 mt-2 overflow-hidden">
                <i className="block h-full rounded bg-brand-500" style={{ width: `${Math.min(100, t.bar)}%` }} />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* treemap */}
      <div className="card">
        <h3 className="text-sm font-semibold text-gray-300 flex items-center justify-between gap-2">
          Capacity Landscape
          <span className="text-[11px] text-gray-600 font-normal">tile = usable capacity · shaded fill = how full · color = cloud · click to filter</span>
        </h3>
        <div ref={treeRef} className="relative w-full mt-3" style={{ height: treeH }}>
          {cells.length === 0 && <div className="text-gray-500 text-sm p-4">No arrays match the current filters.</div>}
          {cells.map((c) => {
            const n = c.node, pct = n.value ? (n.used / n.value) * 100 : 0
            const label = c.w > 54 && c.h > 28
            const sel = dc.has(n.dc)
            return (
              <div key={n.dc} onClick={() => toggle(dc, setDc, n.dc)}
                onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, html: `<b>${n.dc}</b> (${n.csp})<br/>Usable ${fmtCap(n.value)}<br/>Used ${fmtCap(n.used)} (${pct.toFixed(0)}%)<br/>${n.n} arrays` })}
                onMouseLeave={() => setTip(null)}
                className={`absolute rounded-md overflow-hidden cursor-pointer border-2 ${sel ? 'border-white' : 'border-gray-950'} hover:brightness-110`}
                style={{ left: c.x, top: c.y, width: Math.max(0, c.w), height: Math.max(0, c.h), background: CSP_COLOR[n.csp] }}>
                <div className="absolute left-0 right-0 bottom-0 bg-black/30" style={{ height: `${pct}%` }} />
                {label && (
                  <div className="absolute inset-0 p-1.5 flex flex-col justify-between text-white pointer-events-none" style={{ textShadow: '0 1px 2px rgba(0,0,0,.6)' }}>
                    <div className="text-xs font-bold leading-tight">{n.dc}</div>
                    <div className="text-[10px] opacity-90 tabular-nums">{fmtCap(n.value)} · {pct.toFixed(0)}%</div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* breakdown bars */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="card"><h3 className="text-sm font-semibold text-gray-300">Capacity by Cloud <span className="text-[11px] text-gray-600 font-normal">used / usable</span></h3><BarChart dim="csp" colored /></div>
        <div className="card"><h3 className="text-sm font-semibold text-gray-300">Capacity by Vendor</h3><BarChart dim="vendor" /></div>
        <div className="card"><h3 className="text-sm font-semibold text-gray-300">Capacity by Technology</h3><BarChart dim="tech" /></div>
      </div>

      {/* fleet table */}
      <div className="card">
        <div className="flex justify-between items-center flex-wrap gap-2 mb-3">
          <h3 className="text-sm font-semibold text-gray-300">Fleet <span className="text-[11px] text-gray-600 font-normal">{table.length} array{table.length !== 1 ? 's' : ''}</span></h3>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search array or model…"
            className="text-sm px-3 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-brand-500 min-w-[200px]" />
        </div>
        <div className="overflow-x-auto border border-gray-800 rounded-lg">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-800/60">
                {([['name', 'Array'], ['csp', 'Cloud'], ['dc', 'DC'], ['vendor', 'Vendor'], ['tech', 'Tech'], ['model', 'Model'],
                   ['cap_total', 'Usable'], ['used_pct', 'Used %'], ['hosts', 'Hosts'], ['vols', 'Vols'], ['alerts', 'Alerts']] as [keyof FleetArray, string][]).map(([key, lbl]) => {
                  const num = ['cap_total', 'used_pct', 'hosts', 'vols', 'alerts'].includes(key as string)
                  return (
                    <th key={key} onClick={() => setSort(key)}
                      className={`px-2.5 py-2 font-mono text-[10px] uppercase tracking-wide text-gray-500 cursor-pointer hover:text-gray-300 whitespace-nowrap ${num ? 'text-right' : 'text-left'}`}>
                      {lbl} {sortKey === key && <span className="opacity-60">{sortDir < 0 ? '▼' : '▲'}</span>}
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {table.map((a) => {
                const pct = a.used_pct ?? 0
                return (
                  <tr key={a.name} onClick={() => setDetail(a)} className="border-t border-gray-800 cursor-pointer hover:bg-gray-800/40">
                    <td className="px-2.5 py-1.5 font-medium text-white whitespace-nowrap">{a.name}</td>
                    <td className="px-2.5 py-1.5"><span className="text-[10px] font-semibold px-1.5 py-0.5 rounded text-white" style={{ background: CSP_COLOR[a.csp] }}>{a.csp}</span></td>
                    <td className="px-2.5 py-1.5 text-gray-400 whitespace-nowrap">{a.dc}</td>
                    <td className="px-2.5 py-1.5 text-gray-400">{a.vendor}</td>
                    <td className="px-2.5 py-1.5 text-gray-500 text-xs">{a.tech}</td>
                    <td className="px-2.5 py-1.5 text-gray-500 text-xs whitespace-nowrap">{a.model || '—'}</td>
                    <td className="px-2.5 py-1.5 text-right tabular-nums text-gray-300 whitespace-nowrap">{fmtCap(a.cap_total)}</td>
                    <td className="px-2.5 py-1.5 text-right tabular-nums whitespace-nowrap">
                      <span className="inline-block w-16 h-1.5 rounded bg-gray-800 overflow-hidden align-middle mr-1.5">
                        <i className="block h-full rounded" style={{ width: `${Math.min(100, pct)}%`, background: usedColor(pct) }} />
                      </span>{pct.toFixed(0)}%
                    </td>
                    <td className="px-2.5 py-1.5 text-right tabular-nums text-gray-400">{a.hosts}</td>
                    <td className="px-2.5 py-1.5 text-right tabular-nums text-gray-400">{fmtNum(a.vols)}</td>
                    <td className={`px-2.5 py-1.5 text-right tabular-nums font-bold ${a.alerts ? 'text-red-400' : 'text-gray-600 font-normal'}`}>{a.alerts}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* tooltip */}
      {tip && (
        <div className="fixed z-50 pointer-events-none bg-gray-900 border border-gray-700 rounded-lg shadow-xl px-3 py-2 text-xs text-gray-200"
          style={{ left: Math.min(tip.x + 12, window.innerWidth - 200), top: tip.y + 12 }}
          dangerouslySetInnerHTML={{ __html: tip.html }} />
      )}

      {/* detail modal */}
      {detail && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={() => setDetail(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-md p-5 max-h-[85vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between items-start">
              <div>
                <h3 className="text-lg font-semibold text-white">{detail.name}</h3>
                <div className="text-xs text-gray-500 mt-0.5 flex items-center gap-1.5">
                  <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded text-white" style={{ background: CSP_COLOR[detail.csp] }}>{detail.csp}</span>
                  {detail.dc} · {detail.vendor} {detail.model || ''}
                </div>
              </div>
              <button onClick={() => setDetail(null)} className="text-gray-500 hover:text-gray-300"><X size={18} /></button>
            </div>
            <div className="mt-4 text-sm">
              {[
                ['Technology', `${detail.tech}${detail.category ? ' · ' + detail.category : ''}`],
                ['Usable capacity', fmtCap(detail.cap_total)],
                ['Used capacity', `${fmtCap(detail.cap_used)} (${(detail.used_pct ?? 0).toFixed(1)}%)`],
                ['Data reduction', detail.dr != null ? detail.dr.toFixed(2) + ':1' : '—'],
                ['Hosts', String(detail.hosts)],
                ['Volumes', fmtNum(detail.vols)],
                ['Active alerts', String(detail.alerts)],
              ].map(([label, val]) => (
                <div key={label} className="flex justify-between py-1.5 border-t border-gray-800">
                  <span className="text-gray-500">{label}</span>
                  <span className={`tabular-nums ${label === 'Active alerts' && detail.alerts ? 'text-red-400 font-semibold' : 'text-gray-200'}`}>{val}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
