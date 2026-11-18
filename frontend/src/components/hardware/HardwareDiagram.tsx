import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ErrorState from '@/components/common/ErrorState'
import { arraysApi } from '@/api/arrays'
import type { HardwareComponent, HardwareGroup, HardwareInventory, HwStatus } from '@/api/types'
import { formatBytes } from '@/utils/formatters'

// Wireframe of an array's physical hardware: one card per enclosure (chassis,
// controller/node, shelf, cage, drive box, DAE…) with every drive bay, port,
// PSU, fan… drawn as a small part coloured by health.
//
// Colour is never the only cue: every status also has a glyph + label (legend,
// summary, tooltip, details panel, issues list), and empty/unused parts are a
// dashed outline rather than a colour.

const STATUS: Record<HwStatus, { fill: string; tint: string; text: string; label: string; glyph: string }> = {
  healthy: {
    fill: 'bg-green-500/85 border-green-400', tint: 'bg-green-500/10 border-green-500/40',
    text: 'text-green-400', label: 'Healthy', glyph: '●',
  },
  warning: {
    fill: 'bg-yellow-400 border-yellow-200', tint: 'bg-yellow-500/10 border-yellow-500/50',
    text: 'text-yellow-400', label: 'Warning', glyph: '▲',
  },
  critical: {
    fill: 'bg-red-500 border-red-200 ring-2 ring-red-500/40', tint: 'bg-red-500/10 border-red-500/60',
    // U+2715 + VS15 (text presentation) — the heavier ✖ renders as a purple emoji on Windows
    text: 'text-red-400', label: 'Critical', glyph: '✕︎',
  },
  inactive: {
    fill: 'bg-transparent border-dashed border-gray-600', tint: 'bg-transparent border-dashed border-gray-700',
    text: 'text-gray-500', label: 'Empty / not in use', glyph: '○',
  },
  unknown: {
    fill: 'bg-gray-500/60 border-gray-400', tint: 'bg-gray-500/10 border-gray-600',
    text: 'text-gray-400', label: 'Unknown', glyph: '?',
  },
}
const STATUS_ORDER: HwStatus[] = ['critical', 'warning', 'healthy', 'unknown', 'inactive']
const SEVERITY: Record<HwStatus, number> = { critical: 0, warning: 1, unknown: 2, healthy: 3, inactive: 4 }

// A front-panel silhouette per part type.
const SHAPE: Record<string, string> = {
  drive: 'w-[14px] h-[30px] rounded-[2px]',
  port: 'w-[14px] h-[14px] rounded-[2px]',
  psu: 'w-[30px] h-[12px] rounded-[2px]',
  fan: 'w-[14px] h-[14px] rounded-full',
  battery: 'w-[22px] h-[12px] rounded-[2px]',
  memory: 'w-[6px] h-[20px] rounded-[1px]',
  cpu: 'w-[20px] h-[20px] rounded-[3px]',
  module: 'w-[22px] h-[14px] rounded-[2px]',
  sensor: 'w-[10px] h-[10px] rounded-full',
}

const SECTIONS: { types: string[]; label: string }[] = [
  { types: ['controller', 'node'], label: 'Controllers' },
  { types: ['drive'], label: 'Drives' },
  { types: ['port'], label: 'Ports' },
  { types: ['psu'], label: 'Power' },
  { types: ['fan'], label: 'Cooling' },
  { types: ['battery'], label: 'Batteries' },
  { types: ['cpu', 'memory'], label: 'CPU / memory' },
  { types: ['module'], label: 'Modules' },
  { types: ['sensor'], label: 'Sensors' },
]

const TYPE_LABEL: Record<string, string> = {
  drive: 'Drives', port: 'Ports', controller: 'Controllers', node: 'Nodes', psu: 'Power supplies',
  fan: 'Fans', battery: 'Batteries', memory: 'Memory', cpu: 'CPUs', module: 'Modules', sensor: 'Sensors',
}
const SUMMARY_ORDER = ['drive', 'port', 'controller', 'node', 'psu', 'fan', 'battery', 'cpu', 'memory', 'module', 'sensor']

const KIND_LABEL: Record<string, string> = {
  chassis: 'Chassis', controller: 'Controller', node: 'Node', shelf: 'Shelf', cage: 'Cage',
  drive_box: 'Drive box', dpe: 'DPE', dae: 'DAE', head: 'Head', site: 'Site', virtual: 'Virtual', disks: 'Disks',
}

const ATTR_LABEL: Record<string, string> = {
  capacity: 'Capacity', media: 'Media', protocol: 'Protocol', speed: 'Speed', model: 'Model', mode: 'Mode',
  version: 'Version', role: 'Role', label: 'Label', usage: 'Usage', container: 'Container', owner: 'Owner',
  location: 'Location', temperature_c: 'Temperature', temperature: 'Temp status', condition: 'Condition',
  sfp: 'SFP', cache_gb: 'Cache (GB)', link_up: 'Link up', in_use: 'In use', rpm: 'RPM', use: 'Use',
  primary_admin: 'Primary admin', master: 'Master node', voltage: 'Voltage',
}

function fmtAttr(key: string, v: unknown): string {
  if (key === 'capacity' && typeof v === 'number') return formatBytes(v)
  if (key === 'temperature_c' && v != null) return `${v} °C`
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  return String(v)
}

function bySlot(a: HardwareComponent, b: HardwareComponent) {
  const sa = typeof a.slot === 'number' ? a.slot : Number.MAX_SAFE_INTEGER
  const sb = typeof b.slot === 'number' ? b.slot : Number.MAX_SAFE_INTEGER
  return sa - sb || a.name.localeCompare(b.name, undefined, { numeric: true })
}

type Hover = { comp: HardwareComponent; group: HardwareGroup } | null

function tooltip(c: HardwareComponent) {
  const s = STATUS[c.status] ?? STATUS.unknown
  return `${c.name} — ${s.label}${c.raw ? ` (${c.raw})` : ''}${c.detail ? `\n${c.detail}` : ''}`
}

function Cell({ c, g, dim, onHover }: {
  c: HardwareComponent; g: HardwareGroup; dim: boolean; onHover: (h: Hover) => void
}) {
  const s = STATUS[c.status] ?? STATUS.unknown
  return (
    <div
      role="img"
      tabIndex={0}
      aria-label={`${c.name}: ${s.label}`}
      title={tooltip(c)}
      onMouseEnter={() => onHover({ comp: c, group: g })}
      onFocus={() => onHover({ comp: c, group: g })}
      className={`border ${SHAPE[c.type] ?? SHAPE.module} ${s.fill} ${dim ? 'opacity-20' : ''}
        cursor-default transition-transform hover:scale-125 hover:relative hover:z-10
        focus:outline-none focus:ring-2 focus:ring-brand-400`}
    />
  )
}

function Block({ c, g, dim, onHover }: {
  c: HardwareComponent; g: HardwareGroup; dim: boolean; onHover: (h: Hover) => void
}) {
  const s = STATUS[c.status] ?? STATUS.unknown
  const a = c.attrs ?? {}
  const sub = [a.mode, a.role, a.model, a.master ? 'master' : null].filter(Boolean).join(' · ')
  return (
    <div
      tabIndex={0}
      title={tooltip(c)}
      onMouseEnter={() => onHover({ comp: c, group: g })}
      onFocus={() => onHover({ comp: c, group: g })}
      className={`border rounded-md px-2.5 py-1.5 min-w-[110px] ${s.tint} ${dim ? 'opacity-20' : ''}
        focus:outline-none focus:ring-2 focus:ring-brand-400`}
    >
      <div className="flex items-center gap-1.5">
        <span className={`text-[10px] ${s.text}`} aria-hidden>{s.glyph}</span>
        <span className="font-mono text-xs text-gray-100 truncate max-w-[200px]">{c.name}</span>
        <span className="sr-only">{s.label}</span>
      </div>
      {sub && <div className="text-[10px] text-gray-500 truncate max-w-[220px]">{sub}</div>}
    </div>
  )
}

function Enclosure({ g, issuesOnly, onHover }: {
  g: HardwareGroup; issuesOnly: boolean; onHover: (h: Hover) => void
}) {
  const s = STATUS[g.status] ?? STATUS.unknown
  const sections = SECTIONS
    .map((sec) => ({ ...sec, items: g.components.filter((c) => sec.types.includes(c.type)).sort(bySlot) }))
    .filter((sec) => sec.items.length)
  const dim = (c: HardwareComponent) => issuesOnly && (c.status === 'healthy' || c.status === 'inactive')
  const border = g.status === 'critical' ? 'border-red-500/50'
    : g.status === 'warning' ? 'border-yellow-500/40' : 'border-gray-700'

  return (
    <section className={`rounded-lg border ${border} bg-gray-950/40`}>
      <header className="flex items-center gap-2 px-3 py-2 border-b border-gray-800">
        <span className="text-[10px] uppercase tracking-wider text-gray-500 border border-gray-700 rounded px-1.5 py-0.5 shrink-0">
          {KIND_LABEL[g.kind] ?? g.kind}
        </span>
        <span className="text-sm text-gray-100 font-mono truncate">{g.label}</span>
        {g.model && <span className="text-xs text-gray-500 truncate hidden sm:inline">{g.model}</span>}
        <span className={`ml-auto text-xs whitespace-nowrap ${s.text}`}>{s.glyph} {s.label}</span>
      </header>
      <div className="p-3 space-y-3">
        {sections.map((sec) => (
          <div key={sec.label} className="flex gap-3">
            <div className="w-20 shrink-0 text-[10px] uppercase tracking-wide text-gray-500 pt-0.5">
              {sec.types.includes('node') && sec.items.every((c) => c.type === 'node') ? 'Nodes' : sec.label}
              <span className="block normal-case text-gray-600">{sec.items.length}</span>
            </div>
            {sec.types.includes('controller') ? (
              <div className="flex flex-wrap gap-2">
                {sec.items.map((c) => <Block key={c.name} c={c} g={g} dim={dim(c)} onHover={onHover} />)}
              </div>
            ) : sec.label === 'Ports' ? (
              <div className="space-y-1.5 min-w-0">
                {Object.entries(
                  sec.items.reduce<Record<string, HardwareComponent[]>>((acc, c) => {
                    const p = String(c.attrs?.protocol ?? 'Other')
                    ;(acc[p] ||= []).push(c)
                    return acc
                  }, {}),
                ).map(([proto, list]) => (
                  <div key={proto} className="flex items-start gap-2">
                    <span className="w-16 shrink-0 text-[10px] text-gray-500 font-mono pt-0.5">
                      {proto} <span className="text-gray-600">{list.length}</span>
                    </span>
                    <div className="flex flex-wrap gap-[3px]">
                      {list.map((c) => <Cell key={c.name} c={c} g={g} dim={dim(c)} onHover={onHover} />)}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-wrap gap-[3px] items-end min-w-0">
                {sec.items.map((c, i) => <Cell key={`${c.name}-${i}`} c={c} g={g} dim={dim(c)} onHover={onHover} />)}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function Details({ hover }: { hover: Hover }) {
  if (!hover) {
    return <span className="text-gray-500">Hover or tab to a part to see its details.</span>
  }
  const { comp: c, group: g } = hover
  const s = STATUS[c.status] ?? STATUS.unknown
  const attrs = Object.entries(c.attrs ?? {}).filter(([, v]) => v != null && v !== '')
  return (
    <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
      <span className="font-mono text-gray-100">{c.name}</span>
      <span className="text-gray-500">{TYPE_LABEL[c.type]?.replace(/s$/, '') ?? c.type} · {g.label}</span>
      <span className={s.text}>{s.glyph} {s.label}{c.raw ? <span className="text-gray-500"> ({c.raw})</span> : null}</span>
      {c.detail && <span className="text-gray-300">{c.detail}</span>}
      {attrs.map(([k, v]) => (
        <span key={k} className="text-gray-400">
          <span className="text-gray-600">{ATTR_LABEL[k] ?? k}:</span> {fmtAttr(k, v)}
        </span>
      ))}
    </div>
  )
}

export default function HardwareDiagram({ arrayName }: { arrayName: string }) {
  const [hover, setHover] = useState<Hover>(null)
  const [issuesOnly, setIssuesOnly] = useState(false)

  const { data, isLoading, isError, error, refetch } = useQuery<HardwareInventory>({
    queryKey: ['hardware', arrayName],
    queryFn: () => arraysApi.hardware(arrayName),
    refetchInterval: 60_000,
    retry: (n, e: unknown) => (e as { response?: { status?: number } })?.response?.status !== 404 && n < 2,
  })

  const issues = useMemo(() => {
    const out: { c: HardwareComponent; g: HardwareGroup }[] = []
    for (const g of data?.groups ?? []) {
      for (const c of g.components) {
        if (c.status === 'critical' || c.status === 'warning' || c.status === 'unknown') out.push({ c, g })
      }
    }
    return out.sort((a, b) => SEVERITY[a.c.status] - SEVERITY[b.c.status])
  }, [data])

  if (isLoading) return <p className="text-gray-500 text-sm py-6 text-center">Loading hardware…</p>
  if (isError) {
    if ((error as { response?: { status?: number } })?.response?.status === 404) {
      return (
        <p className="text-gray-500 text-sm py-6 text-center">
          No hardware inventory has been collected for this array yet — it appears after the next
          collection cycle (about 5 minutes).
        </p>
      )
    }
    return <ErrorState what="hardware" error={error} onRetry={() => refetch()} />
  }
  if (!data || data.groups.length === 0) {
    return <p className="text-gray-500 text-sm py-6 text-center">This array reported no hardware components.</p>
  }

  const asOf = data.collected_at ? new Date(data.collected_at) : null

  return (
    <div className="space-y-3">
      {/* Summary — counts per part type with a status breakdown */}
      <div className="flex flex-wrap gap-2">
        {SUMMARY_ORDER.filter((t) => data.summary[t]).map((t) => {
          const s = data.summary[t]
          return (
            <div key={t} className="rounded-lg border border-gray-700 bg-gray-800/50 px-3 py-2 min-w-[104px]">
              <div className="text-[10px] uppercase tracking-wide text-gray-500">{TYPE_LABEL[t] ?? t}</div>
              <div className="text-lg font-semibold text-white leading-tight">{s.total ?? 0}</div>
              <div className="flex flex-wrap gap-x-2 text-[11px] mt-0.5">
                {STATUS_ORDER.filter((st) => s[st]).map((st) => (
                  <span key={st} className={STATUS[st].text} title={STATUS[st].label}>
                    {STATUS[st].glyph} {s[st]}
                  </span>
                ))}
              </div>
            </div>
          )
        })}
      </div>

      {/* Legend + controls */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-gray-400">
        {STATUS_ORDER.map((st) => (
          <span key={st} className="flex items-center gap-1.5">
            <span className={`inline-block w-3 h-3 border rounded-[2px] ${STATUS[st].fill.replace(/ring-\S+/g, '')}`} />
            {STATUS[st].glyph} {STATUS[st].label}
          </span>
        ))}
        <label className="ml-auto flex items-center gap-1.5 cursor-pointer select-none">
          <input type="checkbox" className="accent-brand-500" checked={issuesOnly}
                 onChange={(e) => setIssuesOnly(e.target.checked)} />
          Highlight issues only
        </label>
        {asOf && !isNaN(asOf.getTime()) && (
          <span className="text-gray-600">as of {asOf.toLocaleString()}</span>
        )}
      </div>

      {/* Details of the hovered part */}
      <div className="min-h-[44px] rounded-lg border border-gray-800 bg-gray-900/80 px-3 py-2 text-xs" aria-live="polite">
        <Details hover={hover} />
      </div>

      {/* Issues — the non-colour list of everything not healthy */}
      {issues.length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
            {issues.length} part{issues.length === 1 ? '' : 's'} need attention
          </div>
          <ul className="space-y-0.5 text-xs max-h-32 overflow-y-auto">
            {issues.slice(0, 50).map(({ c, g }, i) => (
              <li key={`${g.id}-${c.name}-${i}`}
                  className="flex gap-2 cursor-default"
                  onMouseEnter={() => setHover({ comp: c, group: g })}>
                <span className={`${STATUS[c.status].text} w-20 shrink-0`}>{STATUS[c.status].glyph} {STATUS[c.status].label}</span>
                <span className="text-gray-500 shrink-0">{g.label}</span>
                <span className="font-mono text-gray-200">{c.name}</span>
                {(c.detail || c.raw) && <span className="text-gray-500 truncate">{c.detail || c.raw}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* The wireframe — one card per enclosure */}
      <div className="grid gap-3 lg:grid-cols-2">
        {data.groups.map((g) => (
          <Enclosure key={g.id} g={g} issuesOnly={issuesOnly} onHover={setHover} />
        ))}
      </div>
    </div>
  )
}
