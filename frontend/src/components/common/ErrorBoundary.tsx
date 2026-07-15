import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertOctagon, RotateCw } from 'lucide-react'

interface Props {
  children: ReactNode
  /** Label for the area being guarded — shown in the fallback, e.g. "Dashboard". */
  area?: string
}

interface State {
  error: Error | null
}

/**
 * Catches render-time exceptions so one broken component cannot white-screen the
 * whole app. Without this, any throw during render unmounts the entire tree and
 * the operator sees a blank page with no indication of what failed.
 *
 * Note this only catches errors thrown during rendering, lifecycle methods, and
 * constructors of the tree below it. It does NOT catch errors in event handlers,
 * async code, or data fetching — react-query surfaces those via `isError`, which
 * pages should handle with <ErrorState />.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep this visible in the browser console — there is no error reporting
    // backend to forward to yet.
    console.error('[ErrorBoundary] render failed:', error, info.componentStack)
  }

  handleReset = () => this.setState({ error: null })

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    const { area } = this.props

    return (
      <div className="flex items-center justify-center p-8">
        <div className="max-w-lg w-full bg-gray-900 border border-red-900/60 rounded-lg p-6">
          <div className="flex items-center gap-2 mb-3">
            <AlertOctagon size={18} className="text-red-500 shrink-0" />
            <h2 className="text-sm font-semibold text-gray-100">
              {area ? `${area} failed to render` : 'Something went wrong'}
            </h2>
          </div>

          <p className="text-xs text-gray-400 mb-4">
            This is a bug in the UI, not a problem with your storage. The rest of the
            app is still usable — check the browser console for the full stack trace.
          </p>

          <pre className="text-[11px] text-red-300 bg-gray-950 border border-gray-800 rounded p-3 mb-4 overflow-x-auto whitespace-pre-wrap break-words">
            {error.message || String(error)}
          </pre>

          <button
            onClick={this.handleReset}
            className="inline-flex items-center gap-1.5 text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-200 rounded-lg px-3 py-1.5 transition-colors"
          >
            <RotateCw size={13} />
            Try again
          </button>
        </div>
      </div>
    )
  }
}
