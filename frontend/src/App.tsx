import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from '@/components/layout/Layout'
import Dashboard from '@/pages/Dashboard'
import Volumes from '@/pages/Volumes'
import Hosts from '@/pages/Hosts'
import Alerts from '@/pages/Alerts'
import Analytics from '@/pages/Analytics'
import Capacity from '@/pages/Capacity'
import Settings from '@/pages/Settings'
// Logs page is now a tab inside Settings — /logs redirects to /settings
import NotFound from '@/pages/NotFound'
import ChatPanel from '@/components/chat/ChatPanel'
import ErrorBoundary from '@/components/common/ErrorBoundary'

export default function App() {
  return (
    <ErrorBoundary area="USM">
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="volumes" element={<Volumes />} />
          <Route path="hosts" element={<Hosts />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="capacity" element={<Capacity />} />
          <Route path="chat" element={<ChatPanel />} />
          <Route path="settings" element={<Settings />} />
          <Route path="logs" element={<Navigate to="/settings" replace />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  )
}
