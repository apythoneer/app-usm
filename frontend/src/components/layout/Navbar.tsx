import { RefreshCw } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'

export default function Navbar() {
  const qc = useQueryClient()

  const refresh = () => {
    qc.invalidateQueries()
    toast.success('Data refreshed')
  }

  return (
    <header className="h-14 bg-gray-900 border-b border-gray-800 flex items-center justify-between px-6 shrink-0">
      <h1 className="text-sm font-medium text-gray-300">Storage Intelligence Platform</h1>
      <button onClick={refresh} className="btn-ghost flex items-center gap-2">
        <RefreshCw size={14} />
        Refresh
      </button>
    </header>
  )
}
