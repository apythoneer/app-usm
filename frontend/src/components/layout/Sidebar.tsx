import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, HardDrive, Server, Bell, BarChart2, Settings,
} from 'lucide-react'
import { clsx } from 'clsx'

const NAV = [
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/volumes',   icon: HardDrive,       label: 'Volumes' },
  { to: '/hosts',     icon: Server,          label: 'Hosts' },
  { to: '/alerts',    icon: Bell,            label: 'Alerts' },
  { to: '/analytics', icon: BarChart2,       label: 'Analytics' },
  { to: '/settings',  icon: Settings,        label: 'Settings' },
]

export default function Sidebar() {
  return (
    <aside className="w-56 bg-gray-900 border-r border-gray-800 flex flex-col shrink-0">
      {/* Logo */}
      <div className="h-14 flex items-center px-4 border-b border-gray-800">
        <span className="text-brand-500 font-bold text-lg tracking-tight">USM</span>
        <span className="text-gray-400 text-xs ml-2 mt-0.5">v2.0</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-4 px-2 space-y-1">
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                isActive
                  ? 'bg-brand-600/20 text-brand-400'
                  : 'text-gray-400 hover:text-white hover:bg-gray-800'
              )
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="p-4 border-t border-gray-800 text-xs text-gray-600">
        Unified Storage Monitoring
      </div>
    </aside>
  )
}
