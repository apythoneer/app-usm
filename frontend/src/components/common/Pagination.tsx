interface Props {
  total: number
  offset: number
  limit: number
  onChange: (offset: number) => void
}

/**
 * Server-side pagination control for the Volumes / Hosts / Alerts tables.
 *
 * Previously copy-pasted into all three pages. The copies were logically
 * identical (differing only in brace style), so this extraction is behaviour-
 * preserving — it keeps the same 7-page sliding window and the same
 * "1–50 of 45,683" summary.
 */
export default function Pagination({ total, offset, limit, onChange }: Props) {
  const totalPages = Math.ceil(total / limit)
  const currentPage = Math.floor(offset / limit) + 1
  if (totalPages <= 1) return null

  function goTo(page: number) {
    onChange((page - 1) * limit)
  }

  return (
    <div className="flex items-center justify-between py-3 px-1 border-t border-gray-800 mt-2">
      <span className="text-xs text-gray-500">
        {offset + 1}–{Math.min(offset + limit, total)} of {total.toLocaleString()}
      </span>
      <div className="flex gap-1">
        {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
          // Slide a 7-page window so the current page stays roughly centred
          // once there are more than 7 pages.
          let page: number
          if (totalPages <= 7) {
            page = i + 1
          } else if (currentPage <= 4) {
            page = i + 1
          } else if (currentPage >= totalPages - 3) {
            page = totalPages - 6 + i
          } else {
            page = currentPage - 3 + i
          }
          return (
            <button
              key={page}
              onClick={() => goTo(page)}
              className={`text-xs px-2.5 py-1 rounded ${
                page === currentPage
                  ? 'bg-brand-600/30 text-brand-400'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800'
              }`}
            >
              {page}
            </button>
          )
        })}
      </div>
    </div>
  )
}
