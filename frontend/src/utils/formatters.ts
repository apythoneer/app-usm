export function formatBytes(bytes?: number | null, decimals = 1): string {
  if (bytes == null || bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1)
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(decimals))} ${sizes[i]}`
}

// Format a value already expressed in TB, wrapping into PB / EB so fleet-scale
// numbers stay readable (e.g. 12480 TB -> "12.48 PB"). Base-1000 to match the
// TB figures the backend reports.
export function formatTB(tb?: number | null): string {
  if (tb == null) return '—'
  if (tb >= 1_000_000) return `${(tb / 1_000_000).toFixed(2)} EB`
  if (tb >= 1_000) return `${(tb / 1_000).toFixed(2)} PB`
  return `${tb.toFixed(1)} TB`
}

export function formatIOPS(iops?: number | null): string {
  if (iops == null) return '—'
  if (iops >= 1_000_000) return `${(iops / 1_000_000).toFixed(1)}M`
  if (iops >= 1_000) return `${(iops / 1_000).toFixed(1)}K`
  return iops.toFixed(0)
}

export function formatLatency(us?: number | null): string {
  if (us == null) return '—'
  if (us >= 1_000) return `${(us / 1_000).toFixed(1)} ms`
  return `${us.toFixed(0)} µs`
}

export function formatPct(pct?: number | null): string {
  if (pct == null) return '—'
  return `${pct.toFixed(1)}%`
}

export function formatReduction(ratio?: number | null): string {
  if (ratio == null) return '—'
  return `${ratio.toFixed(2)}:1`
}

export function severityColor(severity?: string): string {
  switch (severity?.toLowerCase()) {
    case 'critical': return 'text-red-400'
    case 'warning':  return 'text-yellow-400'
    case 'info':     return 'text-blue-400'
    default:         return 'text-gray-400'
  }
}

export function severityBg(severity?: string): string {
  switch (severity?.toLowerCase()) {
    case 'critical': return 'bg-red-500/10 text-red-400 border-red-500/20'
    case 'warning':  return 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20'
    case 'info':     return 'bg-blue-500/10 text-blue-400 border-blue-500/20'
    default:         return 'bg-gray-500/10 text-gray-400 border-gray-500/20'
  }
}

export function usedPctColor(pct?: number | null): string {
  if (pct == null) return 'bg-gray-600'
  if (pct >= 90) return 'bg-red-500'
  if (pct >= 75) return 'bg-yellow-500'
  return 'bg-brand-500'
}
