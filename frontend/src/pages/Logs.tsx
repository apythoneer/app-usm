import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, RefreshCw, Pause, Play } from 'lucide-react'
import { settingsApi } from '@/api/settings'

type Level = 'ALL' | 'ERROR' | 'WARN+' | 'INFO+'

function lineLevel(line: string): 'ERROR' | 'WARNING' | 'INFO' | 'DEBUG' {
  if (/\[ERROR\]|\bERROR\b/i.test(line))   return 'ERROR'
  if (/\[WARNING\]|\bWARNING\b|\bWARN\b/i.test(line)) return 'WARNING'
  if (/\[INFO\]|\bINFO\b/i.test(line))     return 'INFO'
  return 'DEBUG'
}

function lineClass(line: string): string {
  const lvl = lineLevel(line)
  if (lvl === 'ERROR')   return 'text-red-400'
  if (lvl === 'WARNING') return 'text-yellow-400'
  if (lvl === 'INFO')    return 'text-gray-300'
  return 'text-gray-600'
}

function passesFilter(line: string, level: Level): boolean {
  if (level === 'ALL') return true
  const lvl = lineLevel(line)
  if (level === 'ERROR') return lvl === 'ERROR'
  if (level === 'WARN+') return lvl === 'ERROR' || lvl === 'WARNING'
  if (level === 'INFO+') return lvl !== 'DEBUG'
  return true
}

export default function Logs() {
  const [lines, setLines]     = useState(500)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [levelFilter, setLevelFilter] = useState<Level>('ALL')
  const bottomRef = useRef<HTMLDivElement>(null)

  const { data, isFetching, refetch } = useQuery({
    queryKey: ['logs', lines],
    queryFn: () => settingsApi.getLogs(lines),
    refetchInterval: autoRefresh ? 5000 : false,
  })

  // Auto-scroll to bottom on new data
  useEffect(() => {
    if (autoRefresh) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [data, autoRefresh])

  const allLines: string[] = data?.data ?? []
  const filtered = allLines.filter((l) => passesFilter(l, levelFilter))

  function handleDownload() {
    const blob = new Blob([allLines.join('\n')], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `usm_backend_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.log`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex flex-col h-full space-y-3">
      {/* Toolbar */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h2 className="text-xl font-semibold text-white">Logs</h2>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Level filter */}
          {(['ALL', 'INFO+', 'WARN+', 'ERROR'] as Level[]).map((l) => (
            <button
              key={l}
              onClick={() => setLevelFilter(l)}
              className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                levelFilter === l
                  ? 'bg-brand-600/20 text-brand-400 border-brand-500/30'
                  : 'text-gray-500 border-gray-700 hover:border-gray-500'
              }`}
            >
              {l}
            </button>
          ))}

          {/* Lines selector */}
          <select
            value={lines}
            onChange={(e) => setLines(Number(e.target.value))}
            className="bg-gray-800 border border-gray-700 text-xs text-gray-300 rounded-lg px-2 py-1.5 focus:outline-none"
          >
            {[100, 500, 1000, 2000].map((n) => (
              <option key={n} value={n}>{n} lines</option>
            ))}
          </select>

          {/* Auto-refresh toggle */}
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border transition-colors ${
              autoRefresh
                ? 'bg-green-500/10 text-green-400 border-green-500/20'
                : 'text-gray-500 border-gray-700 hover:border-gray-500'
            }`}
          >
            {autoRefresh ? <><Pause size={11} /> Live</> : <><Play size={11} /> Paused</>}
          </button>

          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-50"
          >
            <RefreshCw size={12} className={isFetching ? 'animate-spin' : ''} />
          </button>

          <button
            onClick={handleDownload}
            disabled={allLines.length === 0}
            className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-50"
            title="Download log"
          >
            <Download size={12} />
          </button>
        </div>
      </div>

      {/* Stats bar */}
      <div className="text-xs text-gray-600">
        {filtered.length} / {allLines.length} lines
        {autoRefresh && <span className="ml-2 text-green-500/60">● auto-refresh 5s</span>}
      </div>

      {/* Log viewer */}
      <div className="flex-1 bg-gray-950 border border-gray-800 rounded-lg overflow-auto font-mono text-xs leading-5 p-4 min-h-[400px] max-h-[calc(100vh-260px)]">
        {allLines.length === 0 ? (
          <p className="text-gray-600">No log data available.</p>
        ) : (
          filtered.map((line, i) => (
            <div key={i} className={`whitespace-pre-wrap break-all ${lineClass(line)}`}>
              {line}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
