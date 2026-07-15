import { AlertTriangle, RotateCw } from 'lucide-react'
import { clsx } from 'clsx'

interface Props {
  /** What failed to load, e.g. "arrays", "alerts". Used in the message. */
  what: string
  /** The error from react-query's `error` field. */
  error?: unknown
  /** react-query's `refetch` — renders a Retry button when provided. */
  onRetry?: () => void
  className?: string
}

/** Pull a useful message out of an axios error without leaking a stack trace. */
function describe(error: unknown): string {
  if (!error) return 'Unknown error'
  const e = error as { response?: { status?: number }; message?: string }
  const status = e.response?.status
  if (status === 401 || status === 403) return `Not authorized (HTTP ${status})`
  if (status === 404) return `Not found (HTTP 404)`
  if (status && status >= 500) return `Backend error (HTTP ${status})`
  if (e.message?.toLowerCase().includes('timeout')) return 'Request timed out'
  if (e.message?.toLowerCase().includes('network')) return 'Cannot reach the backend'
  return e.message || 'Unknown error'
}

/**
 * Inline error state for a failed query.
 *
 * Why this exists: pages previously destructured only `{ data, isLoading }`, so a
 * failed request fell through to the empty-state branch. An API outage rendered
 * as "No arrays found — check collectors are running", which is actively
 * misleading: it reports a backend failure as an empty fleet and points the
 * operator at the wrong subsystem. For a monitoring tool, "I don't know" must
 * never render as "everything is fine / nothing here".
 */
export default function ErrorState({ what, error, onRetry, className }: Props) {
  return (
    <div
      className={clsx(
        'bg-gray-900 border border-red-900/60 rounded-lg p-4 flex items-start gap-3',
        className,
      )}
    >
      <AlertTriangle size={16} className="text-red-500 shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1">
        <p className="text-sm text-gray-100 font-medium">Couldn’t load {what}</p>
        <p className="text-xs text-gray-400 mt-1 break-words">
          {describe(error)} — this is a problem reaching the USM backend, not
          necessarily a problem with your storage.
        </p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-3 inline-flex items-center gap-1.5 text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-200 rounded-lg px-2.5 py-1 transition-colors"
          >
            <RotateCw size={12} />
            Retry
          </button>
        )}
      </div>
    </div>
  )
}
