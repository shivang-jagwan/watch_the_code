import React from 'react'
import { Link } from 'react-router-dom'
import { Toast } from '../components/Toast'
import {
  generateTimetableGlobal,
  listTimeSlots,
  solveTimetableGlobal,
  pollRunUntilDone,
  validateTimetable,
  type SolverConflict,
  type SolveTimetableResponse,
  type RunDetail,
  type ValidateTimetableResponse,
  type ValidationIssue,
} from '../api/solver'
import { useLayoutContext } from '../components/Layout'

function fmtOrtoolsStatus(code: unknown): string {
  const n = typeof code === 'number' ? code : Number(code)
  if (!Number.isFinite(n)) return String(code)
  const map: Record<number, string> = {
    0: 'UNKNOWN (often timeout)',
    1: 'MODEL_INVALID',
    2: 'FEASIBLE',
    3: 'INFEASIBLE',
    4: 'OPTIMAL',
  }
  return map[n] ? `${n} — ${map[n]}` : String(n)
}

function prettyDiagType(t: unknown): string {
  const s = String(t ?? '')
  return s
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b([a-z])/g, (m) => m.toUpperCase())
}

export function GenerateTimetable() {
  const { programCode } = useLayoutContext()
  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [slotCount, setSlotCount] = React.useState<number | null>(null)

  const [seed, setSeed] = React.useState<string>('')
  const [maxTimeSeconds, setMaxTimeSeconds] = React.useState<number>(300)
  const [relaxTeacherLoadLimits, setRelaxTeacherLoadLimits] = React.useState(false)
  const [requireOptimal, setRequireOptimal] = React.useState(true)

  const [lastRun, setLastRun] = React.useState<SolveTimetableResponse | null>(null)
  const [lastValidationConflicts, setLastValidationConflicts] = React.useState<SolverConflict[]>([])
  const [lastValidation, setLastValidation] = React.useState<ValidateTimetableResponse | null>(null)
  const [pollStatus, setPollStatus] = React.useState<string | null>(null)

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  async function refresh() {
    setLoading(true)
    try {
      const slots = await listTimeSlots()
      setSlotCount(slots.length)
    } catch (e: any) {
      showToast(`Preflight failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  React.useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programCode])

  const missingTimeSlots = slotCount === 0
  const canRun = !loading && slotCount != null && !missingTimeSlots

  async function onValidate() {
    const pc = programCode.trim()
    if (!pc) {
      showToast('Select a program first', 3000)
      return
    }
    setLoading(true)
    setLastValidationConflicts([])
    setLastValidation(null)
    setLastRun(null)
    try {
      const res = await validateTimetable({ program_code: pc })
      setLastValidation(res)
      setLastValidationConflicts([...res.errors, ...res.warnings])
      const issueCount = res.errors.length + res.capacity_issues.length
      if (res.status === 'VALID') {
        showToast('Validation passed — ready to solve.')
      } else if (res.status === 'WARNINGS') {
        showToast(`Validation passed with ${res.warnings.length} warning(s).`)
      } else {
        showToast(`Validation failed: ${issueCount} issue(s) found.`, 4000)
      }
    } catch (e: any) {
      showToast(`Validate failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function onSolve() {
    const pc = programCode.trim()
    if (!pc) {
      showToast('Select a program first', 3000)
      return
    }
    setLoading(true)
    setPollStatus(null)
    setLastValidationConflicts([])
    setLastRun(null)
    try {
      const s = seed.trim() === '' ? null : Number(seed)
      const res = await solveTimetableGlobal({
        program_code: pc,
        seed: Number.isFinite(s as any) ? s : null,
        max_time_seconds: Number(maxTimeSeconds),
        relax_teacher_load_limits: Boolean(relaxTeacherLoadLimits),
        require_optimal: Boolean(requireOptimal),
      })

      if (res.status === 'RUNNING') {
        // Backend accepted the job — poll until done
        setLastRun(res)
        setPollStatus('Solver running on server…')
        showToast('Solver started — polling for completion…', 4000)
        const detail: RunDetail = await pollRunUntilDone(
          res.run_id,
          (d) => {
            const elapsed = d.notes ? ` (${d.notes.slice(0, 60)})` : ''
            setPollStatus(`Solving… status: ${d.status}${elapsed}`)
          },
        )
        // Build a compatible response shape from the RunDetail + saved _solver_result
        const sr: Record<string, any> = (detail.parameters as any)?._solver_result ?? {}
        const finalRun: SolveTimetableResponse = {
          run_id: detail.id,
          status: detail.status as SolveTimetableResponse['status'],
          entries_written: sr.entries_written ?? detail.entries_total ?? 0,
          conflicts: [],
          reason_summary: sr.reason_summary ?? detail.notes ?? null,
          diagnostics: sr.diagnostics ?? [],
          objective_score: sr.objective_score ?? null,
          warnings: sr.warnings ?? [],
          solver_stats: sr.solver_stats ?? {},
          best_bound: sr.best_bound ?? null,
          optimality_gap: sr.optimality_gap ?? null,
          solve_time_seconds: sr.solve_time_seconds ?? null,
          message: sr.message ?? null,
        }
        setLastRun(finalRun)
        setPollStatus(null)
        showToast(`Solve complete: ${finalRun.status}`)
      } else {
        // Synchronous response (validation failure, error, etc.)
        setLastRun(res)
        showToast(`Solve status: ${res.status}`)
      }
    } catch (e: any) {
      showToast(`Solve failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
      setPollStatus(null)
    }
  }

  return (
    <div className="space-y-6">
      <Toast message={toast} />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-slate-900">Generate Timetable</div>
          <div className="mt-1 text-sm text-slate-600">
            Run the solver after completing required setup.
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
            onClick={refresh}
            disabled={loading}
          >
            {loading ? 'Checking…' : 'Re-check'}
          </button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-3xl border bg-white p-5">
          <div className="text-sm font-semibold text-slate-900">Preflight</div>
          <div className="mt-1 text-xs text-slate-500">
            Quick checks to prevent avoidable solver failures.
          </div>

          <div className="mt-4 space-y-3">
            <div
              className={
                'rounded-2xl border p-4 ' +
                (slotCount == null
                  ? 'bg-slate-50 text-slate-700'
                  : missingTimeSlots
                    ? 'border-amber-200 bg-amber-50'
                    : 'border-emerald-200 bg-emerald-50')
              }
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-slate-900">Time Slots</div>
                  <div className="mt-1 text-sm text-slate-700">
                    {slotCount == null
                      ? 'Checking…'
                      : missingTimeSlots
                        ? 'No time slots configured.'
                        : `${slotCount} time slots configured.`}
                  </div>
                  {missingTimeSlots ? (
                    <div className="mt-2 text-xs text-slate-600">
                      Generate time slots first to define the day/period grid.
                    </div>
                  ) : null}
                </div>
                {missingTimeSlots ? (
                  <Link
                    to="/time-slots"
                    className="btn-primary shrink-0 text-sm font-semibold"
                  >
                    Configure
                  </Link>
                ) : (
                  <Link
                    to="/time-slots"
                    className="btn-secondary shrink-0 text-sm font-medium text-slate-800"
                  >
                    View
                  </Link>
                )}
              </div>
            </div>

            <div className="rounded-2xl border bg-slate-50 p-4">
              <div className="text-sm font-semibold text-slate-900">Next checks</div>
              <div className="mt-1 text-sm text-slate-600">
                Teachers, subjects, sections, rooms, curriculum, and elective blocks checks will appear here.
              </div>
            </div>
          </div>
        </div>

        <div className="rounded-3xl border bg-white p-5">
          <div className="text-sm font-semibold text-slate-900">Solver</div>
          <div className="mt-1 text-xs text-slate-500">
            Program-wide solve. Schedules all active sections across all years and semesters in one model.
          </div>

          <div className="mt-4 grid gap-3">
            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <label className="text-xs font-medium text-slate-600">Program</label>
                <div className="mt-1 rounded-2xl border bg-slate-50 px-3 py-2 text-sm text-slate-800">
                  {programCode}
                </div>
              </div>
              <div className="md:col-span-2">
                <label className="text-xs font-medium text-slate-600">Scope</label>
                <div className="mt-1 rounded-2xl border bg-slate-50 px-3 py-2 text-sm text-slate-800">
                  All active sections (all years + all semesters)
                </div>
                <div className="mt-1 text-[11px] text-slate-500">
                  Prevents cross-year teacher overlaps by construction.
                </div>
              </div>
              <div>
                <label htmlFor="solve_seed" className="text-xs font-medium text-slate-600">Seed (optional)</label>
                <input
                  id="solve_seed"
                  className="input-premium mt-1 w-full text-sm"
                  value={seed}
                  onChange={(e) => setSeed(e.target.value)}
                  placeholder="e.g. 42"
                />
              </div>
            </div>

            <div className="mt-2 text-xs text-slate-500">
              Tip: ensure all required years/semesters have subjects, curriculum, windows, and eligible teachers.
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor="solve_max_time" className="text-xs font-medium text-slate-600">Max solve time (seconds)</label>
                <input
                  id="solve_max_time"
                  type="number"
                  min={0.1}
                  step={0.1}
                  className="input-premium mt-1 w-full text-sm"
                  value={maxTimeSeconds}
                  onChange={(e) => setMaxTimeSeconds(Number(e.target.value))}
                />
              </div>
              <div className="flex items-end">
                <label className="checkbox-row w-full rounded-lg border border-white/40 bg-white/70">
                  <input
                    type="checkbox"
                    checked={relaxTeacherLoadLimits}
                    onChange={(e) => setRelaxTeacherLoadLimits(e.target.checked)}
                  />
                  <span className="text-slate-700 font-medium">Relax teacher load limits</span>
                </label>
              </div>

              <div className="flex items-end">
                <label className="checkbox-row w-full rounded-lg border border-white/40 bg-white/70">
                  <input
                    type="checkbox"
                    checked={requireOptimal}
                    onChange={(e) => setRequireOptimal(e.target.checked)}
                  />
                  <span className="text-slate-700 font-medium">Require OPTIMAL solution</span>
                </label>
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <button
                className="btn-secondary w-full text-sm font-semibold text-slate-900 disabled:opacity-50"
                disabled={!canRun}
                onClick={onValidate}
              >
                {missingTimeSlots ? 'Configure Time Slots to Continue' : loading ? 'Working…' : 'Validate'}
              </button>
              <button
                className="btn-primary w-full text-sm font-semibold disabled:opacity-50"
                disabled={!canRun}
                onClick={onSolve}
              >
                {missingTimeSlots ? 'Configure Time Slots to Continue' : loading ? 'Solving…' : 'Solve now'}
              </button>
            </div>

            {pollStatus ? (
              <div className="rounded-2xl border border-indigo-200 bg-indigo-50 p-3 text-sm text-indigo-800">
                <span className="animate-pulse">⏳</span> {pollStatus}
              </div>
            ) : null}

            {lastRun ? (
              <div className="rounded-2xl border bg-slate-50 p-4">
                <div className="text-sm font-semibold text-slate-900">Last solve</div>
                <div className="mt-1 text-sm text-slate-700">
                  Status:{' '}
                  <span className="font-semibold">
                    {lastRun.status === 'INFEASIBLE'
                      ? '🔴 INFEASIBLE'
                      : lastRun.status === 'ERROR'
                        ? '🟠 ERROR'
                        : lastRun.status === 'OPTIMAL'
                          ? '🟢 OPTIMAL'
                          : lastRun.status === 'SUBOPTIMAL'
                            ? '🟡 SUBOPTIMAL'
                          : lastRun.status === 'FEASIBLE'
                            ? '🟡 FEASIBLE'
                            : lastRun.status === 'RUNNING'
                              ? '🔵 RUNNING'
                              : lastRun.status}
                  </span>
                </div>
                <div className="mt-1 text-sm text-slate-700">Entries written: {lastRun.entries_written}</div>
                <div className="mt-1 text-sm text-slate-700">Conflicts: {lastRun.conflicts.length}</div>

                {lastRun.objective_score != null &&
                (lastRun.status === 'FEASIBLE' || lastRun.status === 'SUBOPTIMAL' || lastRun.status === 'OPTIMAL') ? (
                  <div className="mt-1 text-sm text-slate-700">Objective score: {lastRun.objective_score}</div>
                ) : null}

                {lastRun.solver_stats?.ortools_status != null ? (
                  <div className="mt-1 text-xs text-slate-500">
                    OR-Tools status: {fmtOrtoolsStatus(lastRun.solver_stats.ortools_status)}
                  </div>
                ) : null}

                {lastRun.status === 'INFEASIBLE' ? (
                  <div className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm text-slate-800">
                    <div className="font-semibold">Summary</div>
                    <div className="mt-1">{lastRun.reason_summary ?? 'Solver reported infeasible.'}</div>

                    {Array.isArray(lastRun.diagnostics) && lastRun.diagnostics.length > 0 ? (
                      <div className="mt-3 space-y-2">
                        {lastRun.diagnostics.slice(0, 8).map((d: any, i: number) => (
                          <div key={i} className="rounded-xl border bg-white p-3">
                            <div className="text-sm font-semibold text-slate-900">
                              {i + 1}️⃣ {prettyDiagType(d?.type)}
                            </div>
                            <div className="mt-1 text-sm text-slate-700">{String(d?.explanation ?? '')}</div>
                            {d?.teacher || d?.section || d?.subject ? (
                              <div className="mt-2 text-xs text-slate-500">
                                {d?.teacher ? <span>Teacher: {String(d.teacher)} </span> : null}
                                {d?.section ? <span>Section: {String(d.section)} </span> : null}
                                {d?.subject ? <span>Subject: {String(d.subject)} </span> : null}
                              </div>
                            ) : null}
                          </div>
                        ))}
                        {lastRun.diagnostics.length > 8 ? (
                          <div className="text-xs text-slate-600">Showing first 8 diagnostics.</div>
                        ) : null}

                        <details className="rounded-xl border bg-white p-3">
                          <summary className="cursor-pointer text-sm font-semibold text-slate-900">Show full diagnostic JSON</summary>
                          <pre className="mt-2 max-h-[320px] overflow-auto text-xs text-slate-700">
                            {JSON.stringify(lastRun.diagnostics, null, 2)}
                          </pre>
                        </details>
                      </div>
                    ) : (
                      <div className="mt-2 text-xs text-slate-600">No structured diagnostics produced for this run.</div>
                    )}
                  </div>
                ) : null}

                {Array.isArray(lastRun.warnings) && lastRun.warnings.length > 0 ? (
                  <div className="mt-3 rounded-2xl border border-amber-200 bg-amber-50 p-3 text-sm text-slate-800">
                    <div className="font-semibold">Warnings</div>
                    <ul className="mt-2 list-disc space-y-1 pl-5">
                      {lastRun.warnings.slice(0, 8).map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                    {lastRun.warnings.length > 8 ? (
                      <div className="mt-2 text-xs text-slate-600">Showing first 8 warnings.</div>
                    ) : null}
                  </div>
                ) : null}

                {lastRun.status === 'ERROR' ? (
                  <div className="mt-3 rounded-2xl border border-amber-200 bg-amber-50 p-3 text-sm text-slate-800">
                    Solver returned <span className="font-semibold">ERROR</span> (no timetable entries).
                    Most commonly this is a <span className="font-semibold">timeout</span>.
                    Try increasing <span className="font-semibold">Max solve time</span> (e.g. 30–60s) or enable
                    <span className="font-semibold"> Relax teacher load limits</span>, then solve again.
                  </div>
                ) : null}

                {lastRun.conflicts.length > 0 ? (
                  <div className="mt-3 space-y-2">
                    {lastRun.conflicts.slice(0, 12).map((c, idx) => (
                      <div key={idx} className="rounded-2xl border bg-white p-3">
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
                        {c.metadata && Object.keys(c.metadata).length > 0 ? (
                          <div className="mt-2 text-xs text-slate-500">
                            {c.metadata.ortools_status != null ? (
                              <div>OR-Tools status: {fmtOrtoolsStatus(c.metadata.ortools_status)}</div>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    ))}
                    {lastRun.conflicts.length > 12 ? (
                      <div className="text-xs text-slate-500">Showing first 12 conflicts. See Conflicts page for all.</div>
                    ) : null}
                  </div>
                ) : null}

                <div className="mt-3 flex flex-wrap gap-2">
                  <Link
                    to={`/conflicts?runId=${encodeURIComponent(lastRun.run_id)}`}
                    className="btn-secondary text-sm font-medium text-slate-800"
                  >
                    View in Conflicts
                  </Link>
                  <Link
                    to={`/timetable?runId=${encodeURIComponent(lastRun.run_id)}`}
                    className="btn-primary text-sm font-semibold"
                  >
                    View Timetable Grid
                  </Link>
                </div>
              </div>
            ) : null}

            {lastValidation ? (
              <ValidationPanel result={lastValidation} />
            ) : lastValidationConflicts.length > 0 ? (
              <div className="rounded-2xl border bg-amber-50 p-4">
                <div className="text-sm font-semibold text-slate-900">Validation conflicts</div>
                <div className="mt-2 space-y-2">
                  {lastValidationConflicts.slice(0, 20).map((c, idx) => (
                    <div key={idx} className="rounded-2xl border bg-white p-3">
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
                      {c.metadata && Object.keys(c.metadata).length > 0 ? (
                        <div className="mt-2 text-xs text-slate-600">
                          {c.metadata.ortools_status != null ? (
                            <div>OR-Tools status: {fmtOrtoolsStatus(c.metadata.ortools_status)}</div>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
                {lastValidationConflicts.length > 20 ? (
                  <div className="mt-2 text-xs text-slate-600">Showing first 20.</div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Validation Results Panel ─────────────────────────────────────────────────────

const RESOURCE_TYPE_LABELS: Record<string, string> = {
  TEACHER: 'Teacher Overload',
  ROOM_TYPE: 'Room Shortage',
  SECTION: 'Section Slot Deficit',
  COMBINED_GROUP: 'Combined Group Domain Collapse',
  SUBJECT_ROOM: 'Subject Room Restriction Conflict',
}

function ValidationPanel({ result }: { result: ValidateTimetableResponse }) {
  const { status, errors, warnings, capacity_issues } = result
  const isValid = status === 'VALID'
  const isWarn = status === 'WARNINGS'

  const borderCls = isValid
    ? 'border-emerald-200'
    : isWarn
      ? 'border-amber-200'
      : 'border-rose-200'
  const bgCls = isValid ? 'bg-emerald-50' : isWarn ? 'bg-amber-50' : 'bg-rose-50'
  const badgeCls = isValid
    ? 'bg-emerald-100 text-emerald-700'
    : isWarn
      ? 'bg-amber-100 text-amber-700'
      : 'bg-rose-100 text-rose-700'

  return (
    <div className={`rounded-2xl border p-4 ${borderCls} ${bgCls}`}>
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-semibold text-slate-900">Validation Result</div>
        <span className={`rounded-full px-3 py-0.5 text-xs font-semibold ${badgeCls}`}>
          {isValid ? '✓ VALID' : isWarn ? '⚠ WARNINGS' : '✕ INVALID'}
        </span>
      </div>

      {/* Prerequisite errors */}
      {errors.length > 0 ? (
        <div className="mt-3">
          <div className="mb-2 text-xs font-semibold text-rose-700">
            Configuration Errors ({errors.length})
          </div>
          <div className="space-y-2">
            {errors.slice(0, 12).map((c, i) => (
              <div key={i} className="rounded-xl border bg-white p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="text-sm font-semibold text-slate-900">
                    {prettyDiagType(c.conflict_type)}
                  </div>
                  <span className="shrink-0 rounded-full bg-rose-100 px-2 py-0.5 text-xs font-semibold text-rose-700">
                    ERROR
                  </span>
                </div>
                <div className="mt-1 text-sm text-slate-700">{c.message}</div>
                {c.metadata && Object.keys(c.metadata).length > 0 ? (
                  <div className="mt-2 text-xs text-slate-500">
                    {Object.entries(c.metadata)
                      .slice(0, 4)
                      .map(([k, v]) => (
                        <span key={k} className="mr-3">
                          {k}: {String(v)}
                        </span>
                      ))}
                  </div>
                ) : null}
              </div>
            ))}
            {errors.length > 12 ? (
              <div className="text-xs text-slate-500">Showing first 12 errors.</div>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* Capacity issues */}
      {capacity_issues.length > 0 ? (
        <div className="mt-3">
          <div className="mb-2 text-xs font-semibold text-rose-700">
            Capacity Issues ({capacity_issues.length})
          </div>
          <div className="overflow-x-auto rounded-xl border bg-white">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-slate-50 text-left text-xs text-slate-500">
                  <th className="px-3 py-2">Issue Type</th>
                  <th className="px-3 py-2">Affected</th>
                  <th className="px-3 py-2 text-right">Required</th>
                  <th className="px-3 py-2 text-right">Capacity</th>
                  <th className="px-3 py-2 text-right">Shortage</th>
                </tr>
              </thead>
              <tbody>
                {capacity_issues.map((issue, i) => (
                  <tr key={i} className="border-b last:border-0">
                    <td className="px-3 py-2 font-medium text-slate-900">
                      {RESOURCE_TYPE_LABELS[issue.resource_type ?? ''] ?? prettyDiagType(issue.type)}
                    </td>
                    <td className="px-3 py-2 text-slate-700">{issue.resource ?? '—'}</td>
                    <td className="px-3 py-2 text-right text-slate-700">{issue.required ?? '—'}</td>
                    <td className="px-3 py-2 text-right text-slate-700">{issue.capacity ?? '—'}</td>
                    <td className="px-3 py-2 text-right font-semibold text-rose-600">
                      &minus;{issue.shortage ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* Suggestions */}
          {capacity_issues.some((i) => i.suggestion) ? (
            <div className="mt-3 space-y-1">
              {capacity_issues
                .filter((i) => i.suggestion)
                .map((issue, i) => (
                  <div key={i} className="text-xs text-slate-600">
                    <span className="font-medium">{issue.resource ?? prettyDiagType(issue.type)}:</span>{' '}
                    {issue.suggestion}
                  </div>
                ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Warnings */}
      {warnings.length > 0 ? (
        <div className="mt-3">
          <div className="mb-2 text-xs font-semibold text-amber-700">
            Warnings ({warnings.length})
          </div>
          <div className="space-y-2">
            {warnings.slice(0, 10).map((c, i) => (
              <div key={i} className="rounded-xl border bg-white p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="text-sm font-semibold text-slate-900">
                    {prettyDiagType(c.conflict_type)}
                  </div>
                  <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-700">
                    WARN
                  </span>
                </div>
                <div className="mt-1 text-sm text-slate-700">{c.message}</div>
              </div>
            ))}
            {warnings.length > 10 ? (
              <div className="text-xs text-slate-500">Showing first 10 warnings.</div>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* Valid feedback */}
      {isValid ? (
        <div className="mt-3 text-sm text-emerald-700">
          All checks passed. You can safely run the solver.
        </div>
      ) : null}
    </div>
  )
}
