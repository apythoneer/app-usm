import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, X, RefreshCw, Play, Pause, RotateCw } from 'lucide-react'
import { apiClient } from '@/api/client'
import { settingsApi } from '@/api/settings'
import type { DBTableInfo } from '@/api/types'

// ── Tab nav ───────────────────────────────────────────────────────────────────

const TABS = ['General', 'Notifications', 'Collection', 'Database', 'Scheduler'] as const
type Tab = typeof TABS[number]

function TabNav({ active, setActive }: { active: Tab; setActive: (t: Tab) => void }) {
  return (
    <div className="flex gap-1 border-b border-gray-800 mb-6">
      {TABS.map((t) => (
        <button
          key={t}
          onClick={() => setActive(t)}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
            active === t
              ? 'border-brand-500 text-brand-400'
              : 'border-transparent text-gray-500 hover:text-gray-300'
          }`}
        >
          {t}
        </button>
      ))}
    </div>
  )
}

// ── Shared row ────────────────────────────────────────────────────────────────

function Row({ label, value }: { label: string; value?: string }) {
  return (
    <div className="flex justify-between text-sm border-b border-gray-800 pb-2 last:border-0 last:pb-0">
      <span className="text-gray-500">{label}</span>
      <span className="text-gray-200 font-mono text-xs">{value ?? '—'}</span>
    </div>
  )
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function Toast({ msg, ok }: { msg: string; ok: boolean }) {
  return (
    <div className={`flex items-center gap-2 text-sm px-3 py-2 rounded-lg border ${
      ok ? 'bg-green-500/10 text-green-400 border-green-500/20' : 'bg-red-500/10 text-red-400 border-red-500/20'
    }`}>
      {ok ? <Check size={14} /> : <X size={14} />}
      {msg}
    </div>
  )
}

// ── General tab ───────────────────────────────────────────────────────────────

function GeneralTab({ settings }: { settings: any }) {
  return (
    <div className="space-y-4">
      <div className="card space-y-3">
        <h3 className="text-sm font-semibold text-gray-300">Application</h3>
        <Row label="Version"   value={settings?.version} />
        <Row label="App name"  value={settings?.app_name} />
        <Row label="Database"  value={`${settings?.db_server} / ${settings?.db_database}`} />
        <Row label="Schema"    value={settings?.db_schema} />
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
    </div>
  )
}

// ── Notifications tab ─────────────────────────────────────────────────────────

function NotificationsTab({ settings }: { settings: any }) {
  const [url, setUrl] = useState(settings?.teams_webhook_url ?? '')
  const [saveMsg, setSaveMsg] = useState<{ msg: string; ok: boolean } | null>(null)
  const [testMsg, setTestMsg] = useState<{ msg: string; ok: boolean } | null>(null)

  const { mutate: saveUrl, isPending: saving } = useMutation({
    mutationFn: () => settingsApi.updateNotifications(url),
    onSuccess: () => setSaveMsg({ msg: 'Saved. Resets on restart — update .env for persistence.', ok: true }),
    onError: () => setSaveMsg({ msg: 'Save failed', ok: false }),
  })

  const { mutate: testWebhook, isPending: testing } = useMutation({
    mutationFn: () => settingsApi.testNotifications(),
    onSuccess: () => setTestMsg({ msg: 'Test message sent to Teams!', ok: true }),
    onError: (e: any) => setTestMsg({ msg: e?.response?.data?.detail || 'Test failed', ok: false }),
  })

  return (
    <div className="space-y-4">
      <div className="card space-y-4">
        <h3 className="text-sm font-semibold text-gray-300">Microsoft Teams Webhook</h3>
        <div className="space-y-2">
          <label className="text-xs text-gray-500">Webhook URL (Power Automate or Incoming Webhook)</label>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://prod-xx.westus.logic.azure.com/..."
            className="w-full bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-brand-500"
          />
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => saveUrl()}
            disabled={saving}
            className="px-4 py-1.5 text-sm bg-brand-600 hover:bg-brand-700 text-white rounded-lg disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button
            onClick={() => testWebhook()}
            disabled={testing || !url}
            className="px-4 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-lg disabled:opacity-50"
          >
            {testing ? 'Sending…' : 'Send Test Message'}
          </button>
        </div>
        {saveMsg && <Toast {...saveMsg} />}
        {testMsg && <Toast {...testMsg} />}
        <p className="text-xs text-gray-600">
          Changes take effect immediately but reset on container restart. To persist, update TEAMS_WEBHOOK_URL in .env.
        </p>
      </div>
    </div>
  )
}

// ── Collection tab ────────────────────────────────────────────────────────────

function CollectionTab({ settings, scheduler }: { settings: any; scheduler: any }) {
  const [metrics, setMetrics]   = useState(String(settings?.metrics_interval ?? 60))
  const [volumes, setVolumes]   = useState(String(settings?.volumes_interval ?? 900))
  const [alertsI, setAlertsI]   = useState(String(settings?.alerts_interval ?? 300))
  const [msgs, setMsgs] = useState<Record<string, { msg: string; ok: boolean }>>({})

  const { mutate: updateInterval } = useMutation({
    mutationFn: ({ jobId, seconds }: { jobId: string; seconds: number }) =>
      settingsApi.updateJobInterval(jobId, seconds),
    onSuccess: (_, { jobId }) => setMsgs((m) => ({ ...m, [jobId]: { msg: 'Updated', ok: true } })),
    onError: (e: any, { jobId }) =>
      setMsgs((m) => ({ ...m, [jobId]: { msg: e?.response?.data?.detail || 'Failed', ok: false } })),
  })

  function save(jobId: string, val: string) {
    const n = parseInt(val, 10)
    if (isNaN(n) || n < 10) {
      setMsgs((m) => ({ ...m, [jobId]: { msg: 'Minimum 10 seconds', ok: false } }))
      return
    }
    updateInterval({ jobId, seconds: n })
  }

  function IntervalRow({ label, jobId, value, onChange }: {
    label: string; jobId: string; value: string; onChange: (v: string) => void
  }) {
    return (
      <div className="space-y-1">
        <label className="text-xs text-gray-500">{label}</label>
        <div className="flex gap-2 items-center">
          <input
            type="number"
            min={10}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="w-32 bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:border-brand-500"
          />
          <span className="text-xs text-gray-500">seconds</span>
          <button
            onClick={() => save(jobId, value)}
            className="px-3 py-1.5 text-xs bg-brand-600 hover:bg-brand-700 text-white rounded-lg"
          >
            Save
          </button>
          {msgs[jobId] && <Toast {...msgs[jobId]} />}
        </div>
      </div>
    )
  }

  return (
    <div className="card space-y-5">
      <h3 className="text-sm font-semibold text-gray-300">Collection Intervals</h3>
      <IntervalRow label="Metrics"  jobId="pure_metrics"  value={metrics}  onChange={setMetrics} />
      <IntervalRow label="Volumes"  jobId="pure_volumes"  value={volumes}  onChange={setVolumes} />
      <IntervalRow label="Alerts"   jobId="pure_alerts"   value={alertsI}  onChange={setAlertsI} />
      <p className="text-xs text-gray-600">
        Changes take effect immediately in the running scheduler. To persist across restarts, update METRICS_INTERVAL / VOLUMES_INTERVAL / ALERTS_INTERVAL in docker-compose or .env.
      </p>
    </div>
  )
}

// ── Database tab ──────────────────────────────────────────────────────────────

function DatabaseTab() {
  const { data: tables, isLoading, refetch, isFetching } = useQuery<DBTableInfo[]>({
    queryKey: ['db-info'],
    queryFn: () => settingsApi.getDatabaseInfo(),
    refetchInterval: false,
  })

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-300">Database Tables</h3>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-50"
        >
          <RefreshCw size={12} className={isFetching ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {isLoading ? (
        <p className="text-gray-500 text-sm">Loading…</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-500 text-xs border-b border-gray-800">
              <th className="text-left pb-2 pr-4">Table</th>
              <th className="text-right pb-2 pr-4">Rows</th>
              <th className="text-right pb-2">Last Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {(tables ?? []).map((t) => (
              <tr key={t.table_name} className="hover:bg-gray-800/30">
                <td className="py-2 pr-4 font-mono text-xs text-gray-300">{t.table_name}</td>
                <td className="py-2 pr-4 text-right text-gray-400">
                  {t.row_count != null ? t.row_count.toLocaleString() : <span className="text-red-400 text-xs">{t.error}</span>}
                </td>
                <td className="py-2 text-right text-xs text-gray-500">{t.last_updated || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── Scheduler tab ─────────────────────────────────────────────────────────────

function SchedulerTab({ scheduler, refetchScheduler }: { scheduler: any; refetchScheduler: () => void }) {
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  async function doAction(jobId: string, action: 'run' | 'pause' | 'resume') {
    try {
      await apiClient.post(`/scheduler/jobs/${encodeURIComponent(jobId)}/${action}`)
      setActionMsg(`${jobId} → ${action} OK`)
      refetchScheduler()
    } catch {
      setActionMsg(`${jobId} → ${action} FAILED`)
    }
    setTimeout(() => setActionMsg(null), 3000)
  }

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-300">
          Scheduler
          <span className={`ml-2 text-xs ${scheduler?.running ? 'text-green-400' : 'text-red-400'}`}>
            {scheduler?.running ? '● Running' : '● Stopped'}
          </span>
        </h3>
        <button onClick={refetchScheduler} className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1">
          <RefreshCw size={11} /> Refresh
        </button>
      </div>

      {actionMsg && (
        <div className="text-xs text-brand-400 bg-brand-600/10 border border-brand-500/20 rounded px-3 py-1.5">
          {actionMsg}
        </div>
      )}

      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-500 text-xs border-b border-gray-800">
            <th className="text-left pb-2 pr-4">Job</th>
            <th className="text-left pb-2 pr-4">Trigger</th>
            <th className="text-right pb-2 pr-4">Next Run</th>
            <th className="text-right pb-2">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800/50">
          {(scheduler?.jobs ?? []).map((job: any) => (
            <tr key={job.id} className="hover:bg-gray-800/30">
              <td className="py-2 pr-4 font-mono text-xs text-gray-300">{job.id}</td>
              <td className="py-2 pr-4 text-xs text-gray-500 max-w-[180px] truncate">{job.trigger}</td>
              <td className="py-2 pr-4 text-right text-xs text-gray-500">
                {job.next_run ? new Date(job.next_run).toLocaleTimeString() : <span className="text-yellow-400">paused</span>}
              </td>
              <td className="py-2 text-right">
                <div className="flex gap-1 justify-end">
                  <button onClick={() => doAction(job.id, 'run')}
                    className="p-1 text-gray-400 hover:text-green-400" title="Run now">
                    <Play size={12} />
                  </button>
                  <button onClick={() => doAction(job.id, job.next_run ? 'pause' : 'resume')}
                    className="p-1 text-gray-400 hover:text-yellow-400" title={job.next_run ? 'Pause' : 'Resume'}>
                    {job.next_run ? <Pause size={12} /> : <RotateCw size={12} />}
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Settings page ─────────────────────────────────────────────────────────────

export default function Settings() {
  const [activeTab, setActiveTab] = useState<Tab>('General')

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: () => apiClient.get('/settings').then((r) => r.data),
    refetchInterval: false,
  })

  const { data: scheduler, refetch: refetchScheduler } = useQuery({
    queryKey: ['scheduler'],
    queryFn: () => apiClient.get('/scheduler/status').then((r) => r.data),
  })

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-white">Settings</h2>

      <TabNav active={activeTab} setActive={setActiveTab} />

      {isLoading ? (
        <p className="text-gray-500 text-sm">Loading…</p>
      ) : (
        <>
          {activeTab === 'General'       && <GeneralTab settings={settings} />}
          {activeTab === 'Notifications' && <NotificationsTab settings={settings} />}
          {activeTab === 'Collection'    && <CollectionTab settings={settings} scheduler={scheduler} />}
          {activeTab === 'Database'      && <DatabaseTab />}
          {activeTab === 'Scheduler'     && <SchedulerTab scheduler={scheduler} refetchScheduler={refetchScheduler} />}
        </>
      )}
    </div>
  )
}
