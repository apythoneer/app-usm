import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

export default function Settings() {
  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: () => apiClient.get('/settings').then((r) => r.data),
    refetchInterval: false,
  })

  const { data: scheduler } = useQuery({
    queryKey: ['scheduler'],
    queryFn: () => apiClient.get('/scheduler/status').then((r) => r.data),
  })

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold text-white">Settings</h2>

      {isLoading ? (
        <p className="text-gray-500 text-sm">Loading...</p>
      ) : (
        <>
          <div className="card space-y-3">
            <h3 className="text-sm font-semibold text-gray-300">Application</h3>
            <Row label="Version"   value={settings?.version} />
            <Row label="Database"  value={`${settings?.db_server} / ${settings?.db_database}`} />
            <Row label="Schema"    value={settings?.db_schema} />
            <Row label="Teams Webhook" value={settings?.teams_webhook_configured ? 'Configured' : 'Not configured'} />
          </div>

          <div className="card space-y-3">
            <h3 className="text-sm font-semibold text-gray-300">Collection Intervals</h3>
            <Row label="Metrics"  value={`${settings?.metrics_interval}s`} />
            <Row label="Volumes"  value={`${settings?.volumes_interval}s`} />
            <Row label="Alerts"   value={`${settings?.alerts_interval}s`} />
          </div>

          <div className="card space-y-3">
            <h3 className="text-sm font-semibold text-gray-300">Registered Collectors</h3>
            {Object.entries(settings?.registered_collectors ?? {}).map(([vendor, types]: [string, any]) => (
              <div key={vendor} className="flex gap-3 items-center">
                <span className="text-xs bg-gray-800 text-gray-300 px-2 py-0.5 rounded font-mono">{vendor}</span>
                <div className="flex gap-1">
                  {(types as string[]).map((t) => (
                    <span key={t} className="text-xs bg-brand-600/20 text-brand-400 px-2 py-0.5 rounded">{t}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {scheduler && (
            <div className="card space-y-3">
              <h3 className="text-sm font-semibold text-gray-300">
                Scheduler
                <span className={`ml-2 text-xs ${scheduler.running ? 'text-green-400' : 'text-red-400'}`}>
                  {scheduler.running ? '● Running' : '● Stopped'}
                </span>
              </h3>
              {scheduler.jobs?.map((job: any) => (
                <div key={job.id} className="flex justify-between text-xs">
                  <span className="text-gray-400 font-mono">{job.id}</span>
                  <span className="text-gray-500">Next: {job.next_run ? new Date(job.next_run).toLocaleTimeString() : '—'}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value?: string }) {
  return (
    <div className="flex justify-between text-sm border-b border-gray-800 pb-2 last:border-0 last:pb-0">
      <span className="text-gray-500">{label}</span>
      <span className="text-gray-200 font-mono text-xs">{value ?? '—'}</span>
    </div>
  )
}
