import React from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Toast } from '../components/Toast'
import { useLayoutContext } from '../components/Layout'
import { PremiumSelect } from '../components/PremiumSelect'
import { deleteTimetableRun } from '../api/admin'
import { getRun, listRunConflicts, listRunEntries, listRuns, type RunDetail, type RunSummary, type SolverConflict, type TimetableEntry } from '../api/solver'

function fmtDate(iso: string) {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function fmtOrtoolsStatus(code: unknown): string {
  const n = typeof code === 'number' ? code : Number(code)
  if (!Number.isFinite(n)) return String(code)
  const map: Record<number, string> = {
    0: 'UNKNOWN (often timeout/no solution found yet)',
    1: 'MODEL_INVALID',
    2: 'FEASIBLE',
    3: 'INFEASIBLE',
    4: 'OPTIMAL',
  }
  return map[n] ? `${n} — ${map[n]}` : String(n)
}

function prettyKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b(id|ids)\b/gi, (m) => m.toUpperCase())
    .replace(/\b([a-z])/g, (m) => m.toUpperCase())
}

function prettyDay(n: unknown): string {
  const d = Number(n)
  const map = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
  return Number.isFinite(d) && d >= 0 && d < map.length ? map[d] : String(n)
}

function renderDetails(conflict: SolverConflict): React.ReactNode {
  const details = (conflict.details && Object.keys(conflict.details).length > 0 ? conflict.details : null) ??
    (conflict.metadata && Object.keys(conflict.metadata).length > 0 ? conflict.metadata : null)

  if (!details) return null

  const diagnostics = (details as any).diagnostics
  const reasonSummary = (details as any).reason_summary
  if (Array.isArray(diagnostics) && diagnostics.length > 0) {
    return (
      <div className="mt-3 space-y-3">
        <div className="rounded-2xl border bg-rose-50 p-3 text-sm text-slate-800">
          <div className="font-semibold">Diagnostics summary</div>
          <div className="mt-1">{String(reasonSummary ?? `${diagnostics.length} blocking conflicts detected.`)}</div>
        </div>

        <div className="space-y-2">
          {diagnostics.slice(0, 10).map((d: any, i: number) => (
            <div key={i} className="rounded-2xl border bg-slate-50 p-3 text-sm text-slate-800">
              <div className="font-semibold">
                {i + 1}. {prettyKey(String(d?.type ?? 'DIAGNOSTIC'))}
              </div>
              <div className="mt-1 text-slate-700">{String(d?.explanation ?? '')}</div>
              {d?.teacher || d?.section || d?.subject ? (
                <div className="mt-2 text-xs text-slate-600">
                  {d?.teacher ? <span className="mr-3">Teacher: {String(d.teacher)}</span> : null}
                  {d?.section ? <span className="mr-3">Section: {String(d.section)}</span> : null}
                  {d?.subject ? <span className="mr-3">Subject: {String(d.subject)}</span> : null}
                </div>
              ) : null}
            </div>
          ))}
          {diagnostics.length > 10 ? (
            <div className="text-xs text-slate-600">Showing first 10 diagnostics.</div>
          ) : null}
        </div>

        <details className="rounded-2xl border bg-white p-3">
          <summary className="cursor-pointer text-sm font-semibold text-slate-900">Show full diagnostic JSON</summary>
          <pre className="mt-2 max-h-[420px] overflow-auto text-xs text-slate-700">
            {JSON.stringify(diagnostics, null, 2)}
          </pre>
        </details>
      </div>
    )
  }

  // Friendly layouts for common/high-value conflicts.
  if (conflict.conflict_type === 'TEACHER_LOAD_EXCEEDS_MAX_PER_WEEK') {
    const teacherName = details.teacher_name ?? details.teacher_code ?? details.teacher
    const max = details.max_per_week
    const assigned = details.assigned_slots ?? details.required_slots_per_week
    const diff = details.difference ?? (Number(assigned) - Number(max))
    const sections = details.affected_sections ?? details.affected_section_codes ?? details.affected_section_ids
    const subjects = details.affected_subjects ?? details.affected_subject_codes ?? details.affected_subject_ids

    return (
      <div className="mt-3 rounded-2xl border bg-slate-50 p-3 text-sm text-slate-800">
        <div className="font-semibold">Teacher load details</div>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <div>
            <div className="text-xs text-slate-500">Teacher</div>
            <div className="font-medium">{String(teacherName ?? '—')}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Max per week</div>
            <div className="font-medium">{max ?? '—'}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Assigned</div>
            <div className="font-medium">{assigned ?? '—'}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Over by</div>
            <div className="font-medium">{Number.isFinite(Number(diff)) ? String(diff) : '—'}</div>
          </div>
        </div>

        {Array.isArray(sections) && sections.length ? (
          <div className="mt-3">
            <div className="text-xs font-semibold text-slate-600">Affected sections</div>
            <div className="mt-1 flex flex-wrap gap-2">
              {sections.map((s: any, i: number) => (
                <span key={i} className="rounded-full bg-white px-2 py-0.5 text-xs text-slate-700">
                  {String(s)}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {Array.isArray(subjects) && subjects.length ? (
          <div className="mt-3">
            <div className="text-xs font-semibold text-slate-600">Subjects</div>
            <div className="mt-1 flex flex-wrap gap-2">
              {subjects.map((s: any, i: number) => (
                <span key={i} className="rounded-full bg-white px-2 py-0.5 text-xs text-slate-700">
                  {String(s)}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    )
  }

  if (conflict.conflict_type === 'SPECIAL_TEACHER_WEEKLY_OFF_DAY') {
    const teacherName = details.teacher_name ?? details.teacher_code ?? details.teacher
    const offDay = prettyDay(details.weekly_off_day)
    const lockedDay = prettyDay(details.locked_day ?? details.day_of_week)
    const slotIndex = details.locked_slot_index ?? details.slot_index

    return (
      <div className="mt-3 rounded-2xl border bg-slate-50 p-3 text-sm text-slate-800">
        <div className="font-semibold">Weekly off-day conflict</div>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <div>
            <div className="text-xs text-slate-500">Teacher</div>
            <div className="font-medium">{String(teacherName ?? '—')}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Weekly off day</div>
            <div className="font-medium">{offDay}</div>
          </div>
        </div>

        <div className="mt-3">
          <div className="text-xs font-semibold text-slate-600">Locked event</div>
          <div className="mt-1 grid gap-2 sm:grid-cols-2">
            <div>
              <div className="text-xs text-slate-500">Day</div>
              <div className="font-medium">{lockedDay}</div>
            </div>
            <div>
              <div className="text-xs text-slate-500">Slot</div>
              <div className="font-medium">{slotIndex ?? '—'}</div>
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (
    conflict.conflict_type === 'NO_ROOM_AVAILABLE' ||
    conflict.conflict_type === 'NO_LT_ROOM_AVAILABLE' ||
    conflict.conflict_type === 'SPECIAL_ROOM_CONFLICT' ||
    conflict.conflict_type === 'FIXED_ROOM_CONFLICT'
  ) {
    const section = details.section_code ? `${details.section_code}${details.section_name ? ` — ${details.section_name}` : ''}` : null
    const subject = details.subject_code
      ? `${details.subject_code}${details.subject_name ? ` — ${details.subject_name}` : ''}`
      : details.subject_name
        ? String(details.subject_name)
        : null
    const subjectType = details.subject_type ?? details.subjectType
    const teacher = details.teacher_code
      ? `${details.teacher_code}${details.teacher_name ? ` — ${details.teacher_name}` : ''}`
      : details.teacher_name
        ? String(details.teacher_name)
        : null

    const room = details.room_code ? `${details.room_code}${details.room_name ? ` — ${details.room_name}` : ''}` : null
    const roomType = details.room_type ?? details.roomType

    const day = details.day_of_week ?? details.locked_day ?? details.day
    const slotIndex = details.slot_index ?? details.locked_slot_index
    const startTime = details.start_time
    const endTime = details.end_time

    return (
      <div className="mt-3 rounded-2xl border bg-slate-50 p-3 text-sm text-slate-800">
        <div className="font-semibold">Room assignment details</div>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <div>
            <div className="text-xs text-slate-500">Section</div>
            <div className="font-medium">{section ? String(section) : conflict.section_id ? String(conflict.section_id) : '—'}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Subject</div>
            <div className="font-medium">{subject ? String(subject) : conflict.subject_id ? String(conflict.subject_id) : '—'}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Subject type</div>
            <div className="font-medium">{subjectType ? String(subjectType) : '—'}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Teacher</div>
            <div className="font-medium">{teacher ? String(teacher) : conflict.teacher_id ? String(conflict.teacher_id) : '—'}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Room</div>
            <div className="font-medium">{room ? String(room) : conflict.room_id ? String(conflict.room_id) : '—'}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Room type</div>
            <div className="font-medium">{roomType ? String(roomType) : '—'}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Day</div>
            <div className="font-medium">{day != null ? prettyDay(day) : '—'}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500">Slot</div>
            <div className="font-medium">
              {slotIndex ?? '—'}
              {startTime && endTime ? ` (${String(startTime)}–${String(endTime)})` : ''}
            </div>
          </div>
        </div>
      </div>
    )
  }

  // Generic pretty view
  const entries = Object.entries(details)
  return (
    <div className="mt-3 rounded-2xl border bg-slate-50 p-3 text-sm text-slate-800">
      <div className="font-semibold">Details</div>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {entries.map(([k, v]) => (
          <div key={k} className="rounded-xl bg-white p-2">
            <div className="text-xs font-semibold text-slate-600">{prettyKey(k)}</div>
            {Array.isArray(v) ? (
              <div className="mt-1 space-y-1">
                {v.length === 0 ? <div className="text-slate-500">—</div> : null}
                {v.slice(0, 20).map((x: any, i: number) => (
                  <div key={i} className="text-xs text-slate-700">
                    {typeof x === 'object' ? JSON.stringify(x) : String(x)}
                  </div>
                ))}
                {v.length > 20 ? <div className="text-xs text-slate-500">…and {v.length - 20} more</div> : null}
              </div>
            ) : (
              <div className="mt-1 text-xs text-slate-700">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export function Conflicts() {
  const { programCode, academicYearNumber } = useLayoutContext()
  const [params, setParams] = useSearchParams()
  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [deletingRunId, setDeletingRunId] = React.useState<string | null>(null)

  const [runs, setRuns] = React.useState<RunSummary[]>([])
  const [runScopeFilter, setRunScopeFilter] = React.useState<'ALL' | 'PROGRAM_GLOBAL' | 'YEAR_ONLY'>(
    'PROGRAM_GLOBAL',
  )
  const [selectedRunId, setSelectedRunId] = React.useState<string | null>(null)
  const [detail, setDetail] = React.useState<RunDetail | null>(null)
  const [conflicts, setConflicts] = React.useState<SolverConflict[]>([])
  const [expandedKey, setExpandedKey] = React.useState<string | null>(null)
  const [entries, setEntries] = React.useState<TimetableEntry[]>([])
  const [tab, setTab] = React.useState<'conflicts' | 'entries'>('conflicts')
  const [sectionFilter, setSectionFilter] = React.useState<string>('')

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  function runTag(r: RunSummary): string {
    const scope = String((r as any).parameters?.scope ?? '')
    if (scope === 'PROGRAM_GLOBAL') return 'GLOBAL'
    const year = (r as any).parameters?.academic_year_number
    if (year != null) return `YEAR ${year}`
    return 'LEGACY'
  }

  function runDisplay(r: RunSummary): string {
    const p = (r as any).parameters ?? {}
    const sr = p._solver_result ?? {}
    const runName = sr.run_name ?? p.run_name ?? r.solver_version ?? 'Solver'
    const fitness = sr.best_fitness != null ? ` | fit=${sr.best_fitness}` : ''
    const gens = sr.generation_count != null ? ` | gen=${sr.generation_count}` : ''
    return `${runName}${fitness}${gens}`
  }

  const visibleRuns = React.useMemo(() => {
    return runs.filter((r) => {
      const scope = String((r as any).parameters?.scope ?? '')
      if (runScopeFilter === 'ALL') return true
      if (runScopeFilter === 'PROGRAM_GLOBAL') return scope === 'PROGRAM_GLOBAL'
      if (runScopeFilter === 'YEAR_ONLY') {
        const year = (r as any).parameters?.academic_year_number
        return year != null && Number(year) === Number(academicYearNumber)
      }
      return true
    })
  }, [runs, runScopeFilter, academicYearNumber])

  async function refreshRuns() {
    setLoading(true)
    try {
      const pc = programCode.trim()
      if (!pc) {
        setRuns([])
        setSelectedRunId('')
        setDetail(null)
        setConflicts([])
        setEntries([])
        setSectionFilter('')
        return
      }
      const data = await listRuns({ program_code: pc, limit: 50 })
      setRuns(data)
      const requested = params.get('runId')
      if (requested) {
        setSelectedRunId(requested)
      } else if (!selectedRunId && data.length > 0) {
        const preferred =
          data.find((x) => String((x as any).parameters?.scope ?? '') === 'PROGRAM_GLOBAL') ?? data[0]
        setSelectedRunId(preferred.id)
      }
    } catch (e: any) {
      showToast(`Load runs failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function loadRun(runId: string) {
    setLoading(true)
    try {
      const d = await getRun(runId)
      setDetail(d)
      setConflicts([])
      setEntries([])
      setSectionFilter('')
      const c = await listRunConflicts(runId)
      setConflicts(c)
    } catch (e: any) {
      showToast(`Load run failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function loadEntries(runId: string, sectionCode?: string) {
    setLoading(true)
    try {
      const data = await listRunEntries(runId, sectionCode)
      setEntries(data)
    } catch (e: any) {
      showToast(`Load entries failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function onDeleteRun(runId: string) {
    const ok = window.confirm('Delete this run? This will remove its timetable entries and conflicts.')
    if (!ok) return

    setDeletingRunId(runId)
    try {
      await deleteTimetableRun({ confirm: 'DELETE', run_id: runId })
      showToast('Run deleted')
      if (selectedRunId === runId) {
        setSelectedRunId(null)
        setDetail(null)
        setConflicts([])
        setEntries([])
        setExpandedKey(null)
        setTab('conflicts')
      }

      const p = new URLSearchParams(params)
      if (p.get('runId') === runId) p.delete('runId')
      setParams(p, { replace: true })

      await refreshRuns()
    } catch (e: any) {
      showToast(`Delete run failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setDeletingRunId(null)
    }
  }

  React.useEffect(() => {
    refreshRuns()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programCode, academicYearNumber])

  React.useEffect(() => {
    if (selectedRunId) loadRun(selectedRunId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunId])

  React.useEffect(() => {
    if (!selectedRunId) return
    const p = new URLSearchParams(params)
    p.set('runId', selectedRunId)
    setParams(p, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunId])

  const availableSections = React.useMemo(() => {
    const set = new Set<string>()
    for (const e of entries) set.add(e.section_code)
    return Array.from(set).sort()
  }, [entries])

  return (
    <div className="space-y-6">
      <Toast message={toast} />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-slate-900">Conflicts & Runs</div>
          <div className="mt-1 text-sm text-slate-600">
            Browse solver runs for {programCode}. Year selector is used only for filtering.
          </div>
        </div>
        <button
          className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
          onClick={refreshRuns}
          disabled={loading}
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        <div className="rounded-3xl border bg-white p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm font-semibold text-slate-900">Recent runs</div>
            <PremiumSelect
              ariaLabel="Run scope filter"
              className="text-xs"
              value={runScopeFilter}
              onValueChange={(v) => setRunScopeFilter(v as any)}
              options={[
                { value: 'PROGRAM_GLOBAL', label: 'Program Global' },
                { value: 'YEAR_ONLY', label: 'This Year Only' },
                { value: 'ALL', label: 'All' },
              ]}
            />
          </div>
          <div className="mt-3 space-y-2">
            {visibleRuns.length === 0 ? (
              <div className="rounded-2xl border bg-slate-50 p-4 text-sm text-slate-700">No runs found.</div>
            ) : (
              visibleRuns.map((r) => (
                <div
                  key={r.id}
                  className={
                    'w-full rounded-2xl border p-3 text-left ' +
                    (r.id === selectedRunId ? 'border-slate-900 bg-slate-900 text-white' : 'bg-white hover:bg-slate-50')
                  }
                >
                  <div className="flex items-start justify-between gap-3">
                    <button
                      type="button"
                      onClick={() => setSelectedRunId(r.id)}
                      className="min-w-0 flex-1 text-left"
                      disabled={deletingRunId === r.id}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <div className="text-sm font-semibold">{r.status}</div>
                          <div
                            className={
                              'rounded-full px-2 py-0.5 text-[11px] font-semibold ' +
                              (runTag(r).startsWith('GLOBAL')
                                ? 'bg-emerald-100 text-emerald-800'
                                : 'bg-slate-200 text-slate-800')
                            }
                          >
                            {runTag(r)}
                          </div>
                        </div>
                        <div className="text-xs opacity-80">{fmtDate(r.created_at)}</div>
                      </div>
                      <div className="mt-1 text-xs opacity-80">Run: {r.id}</div>
                      <div className="mt-1 text-xs opacity-80">{runDisplay(r)}</div>
                    </button>

                    <button
                      type="button"
                      className={
                        'rounded-xl border px-3 py-2 text-xs font-semibold ' +
                        (r.id === selectedRunId
                          ? 'border-white/30 bg-white/10 text-white hover:bg-white/15'
                          : 'border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100')
                      }
                      onClick={() => onDeleteRun(r.id)}
                      disabled={deletingRunId === r.id}
                      title="Delete run"
                    >
                      {deletingRunId === r.id ? 'Deleting…' : 'Delete'}
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="rounded-3xl border bg-white p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-900">Run details</div>
              <div className="mt-1 text-xs text-slate-500">{detail ? detail.id : '—'}</div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                className={
                  'rounded-2xl px-4 py-2 text-sm font-medium ' +
                  (tab === 'conflicts' ? 'bg-slate-900 text-white' : 'border bg-white text-slate-800')
                }
                onClick={() => setTab('conflicts')}
              >
                Conflicts ({detail?.conflicts_total ?? conflicts.length})
              </button>
              <button
                className={
                  'rounded-2xl px-4 py-2 text-sm font-medium ' +
                  (tab === 'entries' ? 'bg-slate-900 text-white' : 'border bg-white text-slate-800')
                }
                onClick={() => {
                  setTab('entries')
                  if (selectedRunId && entries.length === 0) loadEntries(selectedRunId)
                }}
              >
                Entries ({detail?.entries_total ?? entries.length})
              </button>

              <button
                className="btn-danger px-4 py-2 text-sm font-semibold disabled:opacity-50"
                disabled={!selectedRunId || deletingRunId === selectedRunId}
                onClick={() => selectedRunId && onDeleteRun(selectedRunId)}
                type="button"
                title="Delete selected run"
              >
                {deletingRunId === selectedRunId ? 'Deleting…' : 'Delete run'}
              </button>
            </div>
          </div>

          {detail ? (
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="rounded-2xl border bg-slate-50 p-3">
                <div className="text-xs font-semibold text-slate-600">Status</div>
                <div className="mt-1 text-sm font-semibold text-slate-900">{detail.status}</div>
                <div className="mt-1 text-xs text-slate-500">Created: {fmtDate(detail.created_at)}</div>
              </div>
              <div className="rounded-2xl border bg-slate-50 p-3">
                <div className="text-xs font-semibold text-slate-600">Notes</div>
                <div className="mt-1 text-xs text-slate-700 whitespace-pre-wrap break-words">
                  {detail.notes && String(detail.notes).trim() ? String(detail.notes) : '—'}
                </div>
              </div>
            </div>
          ) : null}

          {tab === 'conflicts' ? (
            <div className="mt-4">
              {conflicts.length === 0 ? (
                <div className="rounded-2xl border bg-slate-50 p-4 text-sm text-slate-700">No conflicts recorded.</div>
              ) : (
                <div className="space-y-2">
                  {conflicts.map((c, idx) => (
                    <button
                      key={c.id ?? String(idx)}
                      type="button"
                      onClick={() => setExpandedKey((prev) => (prev === (c.id ?? String(idx)) ? null : (c.id ?? String(idx))))}
                      className="w-full rounded-2xl border bg-white p-3 text-left hover:bg-slate-50"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-sm font-semibold text-slate-900">{c.conflict_type}</div>
                        <div
                          className={
                            'rounded-full px-2 py-0.5 text-xs font-semibold ' +
                            (c.severity === 'ERROR'
                              ? 'bg-rose-100 text-rose-700'
                              : c.severity === 'WARN'
                                ? 'bg-amber-100 text-amber-700'
                                : 'bg-slate-100 text-slate-700')
                          }
                        >
                          {c.severity}
                        </div>
                      </div>
                      <div className="mt-1 text-sm text-slate-700">{c.message}</div>
                      {(c.details && Object.keys(c.details).length > 0) || (c.metadata && Object.keys(c.metadata).length > 0) ? (
                        <div className="mt-2 text-xs text-slate-500">
                          {((c.details ?? c.metadata) as any).ortools_status != null ? (
                            <div>OR-Tools status: {fmtOrtoolsStatus(((c.details ?? c.metadata) as any).ortools_status)}</div>
                          ) : (
                            <div>Click to view details</div>
                          )}
                        </div>
                      ) : (
                        <div className="mt-2 text-xs text-slate-400">Click to expand</div>
                      )}

                      <div
                        className={
                          'overflow-hidden transition-all duration-200 ' +
                          (expandedKey === (c.id ?? String(idx)) ? 'max-h-[600px] opacity-100' : 'max-h-0 opacity-0')
                        }
                      >
                        {expandedKey === (c.id ?? String(idx)) ? (
                          <>
                            {renderDetails(c)}
                            {selectedRunId ? (
                              <div className="mt-3 flex flex-wrap gap-2">
                                <Link
                                  to={`/timetable?runId=${encodeURIComponent(selectedRunId)}`}
                                  className="btn-secondary text-xs font-semibold text-slate-800"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  Open timetable
                                </Link>
                              </div>
                            ) : null}
                          </>
                        ) : null}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="text-xs text-slate-500">Tip: filter by section_code to reduce noise.</div>
                <div className="flex items-center gap-2">
                  <input
                    value={sectionFilter}
                    onChange={(e) => setSectionFilter(e.target.value)}
                    placeholder="Section code (optional)"
                    className="input-premium w-52 text-sm"
                  />
                  <button
                    className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
                    disabled={!selectedRunId}
                    onClick={() => selectedRunId && loadEntries(selectedRunId, sectionFilter.trim() || undefined)}
                  >
                    Load
                  </button>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-4 py-3">Section</th>
                      <th className="px-4 py-3">Subject</th>
                      <th className="px-4 py-3">Teacher</th>
                      <th className="px-4 py-3">Room</th>
                      <th className="px-4 py-3">Slot</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200">
                    {entries.length === 0 ? (
                      <tr>
                        <td className="px-4 py-4 text-slate-600" colSpan={5}>
                          No entries loaded.
                        </td>
                      </tr>
                    ) : (
                      entries.slice(0, 500).map((e) => (
                        <tr key={e.id} className="hover:bg-slate-50">
                          <td className="px-4 py-3 font-medium text-slate-900">{e.section_code}</td>
                          <td className="px-4 py-3 text-slate-700">{e.subject_code}</td>
                          <td className="px-4 py-3 text-slate-700">{e.teacher_code}</td>
                          <td className="px-4 py-3 text-slate-700">{e.room_code}</td>
                          <td className="px-4 py-3 text-slate-700">
                            D{e.day_of_week} #{e.slot_index} ({e.start_time}-{e.end_time})
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              {entries.length > 500 ? (
                <div className="text-xs text-slate-500">Showing first 500 entries. Use section filter to narrow.</div>
              ) : null}
              {availableSections.length > 0 ? (
                <div className="text-xs text-slate-500">Loaded sections: {availableSections.join(', ')}</div>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
