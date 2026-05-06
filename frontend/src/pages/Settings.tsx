import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, X, RefreshCw, Play, Pause, RotateCw, Plus, Trash2, Zap, Pencil, Search } from 'lucide-react'
import { apiClient } from '@/api/client'
import { settingsApi, managedArraysApi } from '@/api/settings'
import type { DBTableInfo, ManagedArray, ArrayVerifyResult, Vendor } from '@/api/types'

// ── Tab nav ───────────────────────────────────────────────────────────────────

const TABS = ['General', 'Arrays', 'Notifications', 'Collection', 'Database', 'Scheduler'] as const
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
    onSuccess: () => setSaveMsg({ msg: 'Webhook URL saved.', ok: true }),
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
          Webhook URL is persisted to the database and survives container restarts.
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
            <tr className="border-b border-gray-700/50 bg-gray-800/40">
              <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Table</th>
              <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Rows</th>
              <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Last Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {(tables ?? []).map((t) => (
              <tr key={t.table_name} className="hover:bg-gray-800/30">
                <td className="px-4 py-2 font-mono text-xs text-gray-300">{t.table_name}</td>
                <td className="px-4 py-2 text-right text-gray-400">
                  {t.row_count != null ? t.row_count.toLocaleString() : <span className="text-red-400 text-xs">{t.error}</span>}
                </td>
                <td className="px-4 py-2 text-right text-xs text-gray-500">{t.last_updated || '—'}</td>
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
          <tr className="border-b border-gray-700/50 bg-gray-800/40">
            <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Job</th>
            <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Trigger</th>
            <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Next Run</th>
            <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800/50">
          {(scheduler?.jobs ?? []).map((job: any) => (
            <tr key={job.id} className="hover:bg-gray-800/30">
              <td className="px-4 py-2 font-mono text-xs text-gray-300">{job.id}</td>
              <td className="px-4 py-2 text-xs text-gray-500 max-w-[180px] truncate">{job.trigger}</td>
              <td className="px-4 py-2 text-right text-xs text-gray-500">
                {job.next_run ? new Date(job.next_run).toLocaleTimeString() : <span className="text-yellow-400">paused</span>}
              </td>
              <td className="px-4 py-2 text-right">
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

// ── Add Array Modal ──────────────────────────────────────────────────────────

const GROUP_OPTIONS = ['azure', 'aws', 'gcp', 'on-prem']
const VENDOR_OPTIONS: Vendor[] = ['pure', 'netapp']

function AddArrayModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [name, setName] = useState('')
  const [vendor, setVendor] = useState<Vendor>('pure')
  const [group, setGroup] = useState('')
  const [credKey, setCredKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ArrayVerifyResult | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    if (vendor !== 'pure' && !credKey.trim()) {
      setError('KeePass Key is required for non-Pure arrays')
      return
    }
    setSaving(true)
    setError(null)
    setResult(null)
    try {
      await managedArraysApi.add({
        array_name: name.trim(),
        vendor,
        group_label: group || undefined,
        cred_key: credKey.trim() || undefined,
      })
      onAdded()
      // Auto-verify
      try {
        const vr = await managedArraysApi.verify(name.trim())
        setResult(vr)
      } catch {
        // verify is optional, array was still added
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to add array')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-md mx-4 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <h3 className="text-white font-semibold">Add Array</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white p-1"><X size={16} /></button>
        </div>
        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          <div className="space-y-1">
            <label className="text-xs text-gray-500">Array Hostname</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="purecbs-gp-prod-eus2-01"
              className="w-full bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-brand-500"
              autoFocus
              disabled={!!result}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs text-gray-500">Vendor</label>
              <select
                value={vendor}
                onChange={(e) => setVendor(e.target.value as Vendor)}
                className="w-full bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-brand-500"
                disabled={!!result}
              >
                {VENDOR_OPTIONS.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-gray-500">Group</label>
              <select
                value={group}
                onChange={(e) => setGroup(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-brand-500"
                disabled={!!result}
              >
                <option value="">None</option>
                {GROUP_OPTIONS.map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            </div>
          </div>

          <div className="space-y-1">
            <label className="text-xs text-gray-500">
              KeePass Key {vendor !== 'pure' && <span className="text-red-400">*</span>}
            </label>
            <input
              type="text"
              value={credKey}
              onChange={(e) => setCredKey(e.target.value)}
              placeholder={vendor === 'pure' ? `Auto: PureStorage_API_{hostname}` : 'e.g. NetApp_A400_DDC'}
              className="w-full bg-gray-800 border border-gray-700 text-sm text-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:border-brand-500"
              disabled={!!result}
            />
            <p className="text-[10px] text-gray-600">
              {vendor === 'pure'
                ? 'Optional — auto-derived as PureStorage_API_{hostname} if blank'
                : 'Required — the KeePass entry name for this array\'s credentials'}
            </p>
          </div>

          {error && <Toast msg={error} ok={false} />}

          {result && (
            <div className="space-y-2 p-3 bg-gray-800 rounded-lg">
              <p className="text-xs text-gray-500 uppercase tracking-wide">Verification Result</p>
              <div className="flex gap-4 text-xs">
                <span className={result.keepass_ok ? 'text-green-400' : 'text-red-400'}>
                  KeePass: {result.keepass_ok ? 'OK' : 'FAIL'}
                </span>
                <span className={result.connectivity_ok ? 'text-green-400' : 'text-red-400'}>
                  API: {result.connectivity_ok ? 'OK' : 'FAIL'}
                </span>
                {result.version && <span className="text-gray-400">v{result.version}</span>}
              </div>
              {result.error && <p className="text-xs text-red-400">{result.error}</p>}
            </div>
          )}

          <div className="flex gap-2 pt-2">
            {!result ? (
              <button
                type="submit"
                disabled={saving || !name.trim()}
                className="px-4 py-2 text-sm bg-brand-600 hover:bg-brand-700 text-white rounded-lg disabled:opacity-50"
              >
                {saving ? 'Adding…' : 'Add & Verify'}
              </button>
            ) : (
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 text-sm bg-brand-600 hover:bg-brand-700 text-white rounded-lg"
              >
                Done
              </button>
            )}
            {!result && (
              <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-white">
                Cancel
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Arrays tab ───────────────────────────────────────────────────────────────

function ArraysTab() {
  const qc = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)
  const [verifying, setVerifying] = useState<string | null>(null)
  const [verifyResult, setVerifyResult] = useState<ArrayVerifyResult | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [editArr, setEditArr] = useState<ManagedArray | null>(null)
  const [filter, setFilter] = useState('')
  const [vendorFilter, setVendorFilter] = useState<string>('all')

  const { data: arrays = [], isLoading } = useQuery<ManagedArray[]>({
    queryKey: ['managed-arrays'],
    queryFn: () => managedArraysApi.list(),
  })

  const { mutate: toggleEnabled } = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      managedArraysApi.update(name, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['managed-arrays'] }),
  })

  const { mutate: deleteArray } = useMutation({
    mutationFn: (name: string) => managedArraysApi.remove(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['managed-arrays'] })
      setDeleteConfirm(null)
    },
  })

  async function handleVerify(name: string) {
    setVerifying(name)
    setVerifyResult(null)
    try {
      const r = await managedArraysApi.verify(name)
      setVerifyResult(r)
    } catch {
      setVerifyResult({ array_name: name, keepass_ok: false, connectivity_ok: false, error: 'Request failed' })
    } finally {
      setVerifying(null)
    }
  }

  // Filter arrays
  const vendors = [...new Set(arrays.map((a) => a.vendor))].sort()
  const filtered = arrays.filter((a) => {
    if (vendorFilter !== 'all' && a.vendor !== vendorFilter) return false
    if (filter) {
      const q = filter.toLowerCase()
      return a.array_name.toLowerCase().includes(q)
        || (a.model || '').toLowerCase().includes(q)
        || (a.site || '').toLowerCase().includes(q)
        || (a.cred_key || '').toLowerCase().includes(q)
    }
    return true
  })

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-gray-300">
            Managed Arrays <span className="text-gray-500 text-xs ml-1">({filtered.length} of {arrays.length})</span>
          </h3>
          <div className="flex gap-2">
            <button
              onClick={() => setShowAdd(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-brand-600 hover:bg-brand-700 text-white rounded-lg"
            >
              <Plus size={12} /> Add Array
            </button>
          </div>
        </div>

        {/* Search + Vendor filter */}
        <div className="flex gap-2 mb-3">
          <div className="relative flex-1">
            <Search size={14} className="absolute left-2.5 top-2 text-gray-500" />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search arrays, models, sites..."
              className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-8 pr-3 py-1.5 text-xs text-gray-200 placeholder-gray-500 focus:outline-none focus:border-brand-500"
            />
          </div>
          <select
            value={vendorFilter}
            onChange={(e) => setVendorFilter(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-brand-500"
          >
            <option value="all">All vendors</option>
            {vendors.map((v) => (
              <option key={v} value={v}>{v} ({arrays.filter((a) => a.vendor === v).length})</option>
            ))}
          </select>
        </div>

        {isLoading ? (
          <p className="text-gray-500 text-sm">Loading…</p>
        ) : filtered.length === 0 ? (
          <p className="text-gray-500 text-sm">No arrays match your filter.</p>
        ) : (
          <div className="overflow-x-auto max-h-[60vh] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10">
              <tr className="border-b border-gray-700/50 bg-gray-800">
                <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Array</th>
                <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Vendor</th>
                <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Model</th>
                <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">Site</th>
                <th className="px-3 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">KeePass Key</th>
                <th className="px-3 py-2 text-center text-xs text-gray-500 uppercase tracking-wide">Status</th>
                <th className="px-3 py-2 text-center text-xs text-gray-500 uppercase tracking-wide">On</th>
                <th className="px-3 py-2 text-right text-xs text-gray-500 uppercase tracking-wide">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {filtered.map((arr) => (
                <tr key={arr.array_name} className="hover:bg-gray-800/30">
                  <td className="px-3 py-1.5 font-mono text-xs text-gray-200 max-w-[200px] truncate" title={arr.array_name}>{arr.array_name}</td>
                  <td className="px-3 py-1.5 text-xs text-gray-400 capitalize">{arr.vendor}</td>
                  <td className="px-3 py-1.5 text-xs text-gray-500">{arr.model || '—'}</td>
                  <td className="px-3 py-1.5 text-xs text-gray-500">{arr.site || '—'}</td>
                  <td className="px-3 py-1.5 text-xs text-gray-500 font-mono max-w-[180px] truncate" title={arr.cred_key || 'auto'}>
                    {arr.cred_key || <span className="text-gray-600 italic">none</span>}
                  </td>
                  <td className="px-3 py-1.5 text-center">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                      arr.monitoring_status === 'active' ? 'bg-green-500/10 text-green-400' :
                      arr.monitoring_status === 'no_collector' ? 'bg-yellow-500/10 text-yellow-400' :
                      arr.monitoring_status === 'decomming' ? 'bg-red-500/10 text-red-400' :
                      'bg-gray-500/10 text-gray-400'
                    }`}>
                      {arr.monitoring_status || '?'}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-center">
                    <button
                      onClick={() => toggleEnabled({ name: arr.array_name, enabled: !arr.enabled })}
                      className={`relative w-7 h-3.5 rounded-full transition-colors ${arr.enabled ? 'bg-green-500' : 'bg-gray-600'}`}
                    >
                      <span className={`absolute top-0.5 w-2.5 h-2.5 bg-white rounded-full transition-transform ${arr.enabled ? 'left-3.5' : 'left-0.5'}`} />
                    </button>
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <div className="flex gap-0.5 justify-end">
                      <button
                        onClick={() => setEditArr(arr)}
                        className="flex items-center gap-1 text-xs text-gray-400 hover:text-brand-400 px-1.5 py-1 rounded hover:bg-gray-800"
                        title="Edit array"
                      >
                        <Pencil size={11} />
                      </button>
                      <button
                        onClick={() => handleVerify(arr.array_name)}
                        disabled={verifying === arr.array_name}
                        className="flex items-center gap-1 text-xs text-gray-400 hover:text-brand-400 px-1.5 py-1 rounded hover:bg-gray-800"
                        title="Test connectivity"
                      >
                        <Zap size={11} />
                      </button>
                      {deleteConfirm === arr.array_name ? (
                        <div className="flex items-center gap-1">
                          <button onClick={() => deleteArray(arr.array_name)} className="text-[10px] text-red-400 px-1.5 py-1">Yes</button>
                          <button onClick={() => setDeleteConfirm(null)} className="text-[10px] text-gray-500 px-1 py-1">No</button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setDeleteConfirm(arr.array_name)}
                          className="text-xs text-gray-400 hover:text-red-400 px-1.5 py-1 rounded hover:bg-gray-800"
                          title="Delete"
                        >
                          <Trash2 size={11} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {verifyResult && (
        <div className="card p-3">
          <div className="flex items-center justify-between mb-1">
            <span className="font-mono text-xs text-gray-300">{verifyResult.array_name}</span>
            <button onClick={() => setVerifyResult(null)} className="text-gray-500 hover:text-white"><X size={14} /></button>
          </div>
          <div className="flex gap-4 text-xs">
            <span className={verifyResult.keepass_ok ? 'text-green-400' : 'text-red-400'}>
              KeePass: {verifyResult.keepass_ok ? 'OK' : 'FAIL'}
            </span>
            <span className={verifyResult.connectivity_ok ? 'text-green-400' : 'text-red-400'}>
              API: {verifyResult.connectivity_ok ? 'OK' : 'FAIL'}
            </span>
            {verifyResult.version && <span className="text-gray-400">v{verifyResult.version}</span>}
          </div>
          {verifyResult.error && <p className="text-xs text-red-400 mt-1">{verifyResult.error}</p>}
        </div>
      )}

      {showAdd && (
        <AddArrayModal
          onClose={() => setShowAdd(false)}
          onAdded={() => qc.invalidateQueries({ queryKey: ['managed-arrays'] })}
        />
      )}

      {editArr && (
        <EditArrayModal
          arr={editArr}
          onClose={() => setEditArr(null)}
          onSaved={() => { qc.invalidateQueries({ queryKey: ['managed-arrays'] }); setEditArr(null) }}
        />
      )}
    </div>
  )
}

// ── Edit Array Modal with KeePass picker ──────────────────────────────────────

function EditArrayModal({ arr, onClose, onSaved }: { arr: ManagedArray; onClose: () => void; onSaved: () => void }) {
  const [credKey, setCredKey] = useState(arr.cred_key || '')
  const [fqdn, setFqdn] = useState(arr.array_fqdn || '')
  const [saving, setSaving] = useState(false)
  const [kpFilter, setKpFilter] = useState('')

  const { data: kpData } = useQuery({
    queryKey: ['keepass-entries'],
    queryFn: () => settingsApi.getKeePassEntries(),
    staleTime: 300_000,
  })

  // Flatten KeePass entries for the picker
  const allEntries: { group: string; name: string }[] = []
  if (kpData?.groups) {
    for (const [group, names] of Object.entries(kpData.groups)) {
      for (const name of names) {
        allEntries.push({ group, name })
      }
    }
  }
  const filteredKp = kpFilter
    ? allEntries.filter((e) => e.name.toLowerCase().includes(kpFilter.toLowerCase()) || e.group.toLowerCase().includes(kpFilter.toLowerCase()))
    : allEntries.slice(0, 20)

  async function handleSave() {
    setSaving(true)
    try {
      await managedArraysApi.update(arr.array_name, {
        cred_key: credKey || undefined,
        array_fqdn: fqdn || undefined,
      })
      onSaved()
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-[550px] max-h-[80vh] overflow-y-auto shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <h3 className="text-sm font-semibold text-gray-200">Edit Array: <span className="font-mono text-brand-400">{arr.array_name}</span></h3>
          <button onClick={onClose} className="text-gray-500 hover:text-white"><X size={16} /></button>
        </div>
        <div className="px-5 py-4 space-y-4">
          {/* Info */}
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div><span className="text-gray-500">Vendor:</span> <span className="text-gray-300 capitalize ml-1">{arr.vendor}</span></div>
            <div><span className="text-gray-500">Model:</span> <span className="text-gray-300 ml-1">{arr.model || '—'}</span></div>
            <div><span className="text-gray-500">Site:</span> <span className="text-gray-300 ml-1">{arr.site || '—'}</span></div>
            <div><span className="text-gray-500">Status:</span> <span className="text-gray-300 ml-1">{arr.monitoring_status || '—'}</span></div>
            <div><span className="text-gray-500">Category:</span> <span className="text-gray-300 ml-1">{arr.category || '—'}</span></div>
            <div><span className="text-gray-500">Disposition:</span> <span className="text-gray-300 ml-1">{arr.disposition || '—'}</span></div>
          </div>

          {/* FQDN */}
          <div>
            <label className="text-xs text-gray-500 block mb-1">Array FQDN / Hostname</label>
            <input
              value={fqdn}
              onChange={(e) => setFqdn(e.target.value)}
              placeholder="e.g. array01.corp.intranet"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-xs text-gray-200 placeholder-gray-500 focus:outline-none focus:border-brand-500"
            />
          </div>

          {/* KeePass Credential Key */}
          <div>
            <label className="text-xs text-gray-500 block mb-1">KeePass Credential Key</label>
            <input
              value={credKey}
              onChange={(e) => setCredKey(e.target.value)}
              placeholder="e.g. PureStorage_API_purecbs-gp-prod-eus2-02"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-xs text-gray-200 font-mono placeholder-gray-500 focus:outline-none focus:border-brand-500"
            />
          </div>

          {/* KeePass Browser */}
          <div>
            <label className="text-xs text-gray-500 block mb-1">Browse KeePass Entries ({allEntries.length} available)</label>
            <input
              value={kpFilter}
              onChange={(e) => setKpFilter(e.target.value)}
              placeholder="Search KeePass entries..."
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-xs text-gray-200 placeholder-gray-500 focus:outline-none focus:border-brand-500 mb-2"
            />
            <div className="max-h-[200px] overflow-y-auto bg-gray-800/50 border border-gray-700/50 rounded-lg">
              {filteredKp.length === 0 ? (
                <p className="text-xs text-gray-500 p-3 text-center">{kpFilter ? 'No matches' : 'Type to search...'}</p>
              ) : (
                filteredKp.map((e, i) => (
                  <button
                    key={`${e.group}-${e.name}-${i}`}
                    onClick={() => setCredKey(e.name)}
                    className={`w-full text-left px-3 py-1.5 text-xs hover:bg-gray-700/50 flex justify-between items-center border-b border-gray-800/30 last:border-0 ${
                      credKey === e.name ? 'bg-brand-600/10 text-brand-400' : 'text-gray-300'
                    }`}
                  >
                    <span className="font-mono truncate">{e.name}</span>
                    <span className="text-[10px] text-gray-600 ml-2 shrink-0">{e.group}</span>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-800">
          <button onClick={onClose} className="px-3 py-1.5 text-xs text-gray-400 hover:text-white">Cancel</button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-1.5 text-xs bg-brand-600 hover:bg-brand-700 text-white rounded-lg disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save Changes'}
          </button>
        </div>
      </div>
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
          {activeTab === 'Arrays'        && <ArraysTab />}
          {activeTab === 'Notifications' && <NotificationsTab settings={settings} />}
          {activeTab === 'Collection'    && <CollectionTab settings={settings} scheduler={scheduler} />}
          {activeTab === 'Database'      && <DatabaseTab />}
          {activeTab === 'Scheduler'     && <SchedulerTab scheduler={scheduler} refetchScheduler={refetchScheduler} />}
        </>
      )}
    </div>
  )
}
