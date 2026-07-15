/**
 * Vendor pill used in the Dashboard array table and the Volumes/Hosts/Alerts
 * tables.
 *
 * The colour map, the grey fallback, and the span markup were previously copied
 * verbatim into four page files. They were byte-identical, so adding a vendor
 * (or restyling the pill) meant four edits that could silently drift apart.
 */

const VENDOR_COLORS: Record<string, string> = {
  pure:    'bg-orange-500/10 text-orange-400 border-orange-500/20',
  netapp:  'bg-blue-500/10 text-blue-400 border-blue-500/20',
  hpe:     'bg-green-500/10 text-green-400 border-green-500/20',
  hitachi: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
  dell:    'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
  oracle:  'bg-red-500/10 text-red-400 border-red-500/20',
  // NB: `storagegrid` is a distinct vendor key (scheduler.py remaps NetApp
  // StorageGrid arrays to it) and has no entry, so those arrays render grey.
}

const FALLBACK = 'bg-gray-500/10 text-gray-400 border-gray-500/20'

/** Tailwind classes for a vendor's badge colours. Unknown vendors render grey. */
export function vendorBadgeClass(vendor: string): string {
  return VENDOR_COLORS[vendor] ?? FALLBACK
}

export default function VendorBadge({ vendor }: { vendor: string }) {
  return (
    <span
      className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase border ${vendorBadgeClass(vendor)}`}
    >
      {vendor}
    </span>
  )
}
