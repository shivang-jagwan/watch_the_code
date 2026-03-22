import React from 'react'
import { Link } from 'react-router-dom'
import { Toast } from '../components/Toast'
import {
  generateTimetableGlobal,
  listRuns,
  listRunEntries,
  listTimeSlots,
  solveTimetableGlobal,
  pollRunUntilDone,
  validateTimetable,
  type SolverConflict,
  type SolveTimetableResponse,
  type RunDetail,
  type TimetableEntry,
  type TimeSlot,
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
  const { programCode, academicYearNumber } = useLayoutContext()
  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [slots, setSlots] = React.useState<TimeSlot[]>([])
  const [slotCount, setSlotCount] = React.useState<number | null>(null)

  const [seed, setSeed] = React.useState<string>('')
  const [solverType, setSolverType] = React.useState<'GA_ONLY' | 'HYBRID' | 'CP_SAT_ONLY'>('HYBRID')
  const [gaPopulationSize, setGaPopulationSize] = React.useState<number>(24)
  const [gaGenerations, setGaGenerations] = React.useState<number>(40)
  const [gaCpSatSeconds, setGaCpSatSeconds] = React.useState<number>(1.0)
  const [maxTimeSeconds, setMaxTimeSeconds] = React.useState<number>(300)
  const [relaxTeacherLoadLimits, setRelaxTeacherLoadLimits] = React.useState(false)
  const [requireOptimal, setRequireOptimal] = React.useState(true)

  const [lastRun, setLastRun] = React.useState<SolveTimetableResponse | null>(null)
  const [lastRunEntries, setLastRunEntries] = React.useState<TimetableEntry[]>([])
  const [lastSolverResult, setLastSolverResult] = React.useState<Record<string, any> | null>(null)
  const [lastValidationConflicts, setLastValidationConflicts] = React.useState<SolverConflict[]>([])
  const [lastValidation, setLastValidation] = React.useState<ValidateTimetableResponse | null>(null)
  const [pollStatus, setPollStatus] = React.useState<string | null>(null)
  const [analyticsOpen, setAnalyticsOpen] = React.useState(false)

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  async function refresh() {
    setLoading(true)
    try {
      const allSlots = await listTimeSlots()
      setSlots(allSlots)
      setSlotCount(allSlots.length)
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
  const effectiveSolverType = (lastRun?.solver_type ?? solverType) as 'GA_ONLY' | 'HYBRID' | 'CP_SAT_ONLY'
  const involvesGA = effectiveSolverType === 'GA_ONLY' || effectiveSolverType === 'HYBRID'

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
    setLastRunEntries([])
    setLastSolverResult(null)
    try {
      const s = seed.trim() === '' ? null : Number(seed)
      const res = await solveTimetableGlobal({
        program_code: pc,
        academic_year_number: Number(academicYearNumber),
        solver_type: solverType,
        seed: Number.isFinite(s as any) ? s : null,
        max_time_seconds: Number(maxTimeSeconds),
        relax_teacher_load_limits: Boolean(relaxTeacherLoadLimits),
        require_optimal: Boolean(requireOptimal),
        population_size: solverType === 'CP_SAT_ONLY' ? undefined : Number(gaPopulationSize),
        generations: solverType === 'CP_SAT_ONLY' ? undefined : Number(gaGenerations),
        cp_sat_max_time_seconds: solverType === 'HYBRID' ? Number(gaCpSatSeconds) : undefined,
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
        setLastSolverResult(sr)
        const finalRun: SolveTimetableResponse = {
          run_id: detail.id,
          status: detail.status as SolveTimetableResponse['status'],
          entries_written: sr.entries_written ?? detail.entries_total ?? 0,
          run_name: sr.run_name ?? (detail.parameters as any)?.run_name ?? (solverType === 'CP_SAT_ONLY' ? 'CP-SAT' : null),
          solver_type: sr.solver_type ?? (detail.parameters as any)?.solver_type ?? solverType,
          best_fitness: sr.best_fitness ?? null,
          generation_count: sr.generation_count ?? null,
          hard_constraints_satisfied: sr.hard_constraints_satisfied ?? null,
          cp_sat_repair_applied: sr.cp_sat_repair_applied ?? null,
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
        try {
          const entries = await listRunEntries(detail.id)
          setLastRunEntries(entries)
        } catch {
          setLastRunEntries([])
        }
        setPollStatus(null)
        showToast(`Solve complete: ${finalRun.status}`)
      } else {
        // Synchronous response (validation failure, error, etc.)
        setLastRun(res)
        setLastSolverResult(null)
        setLastRunEntries([])
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
              <div>
                <label htmlFor="solver_type" className="text-xs font-medium text-slate-600">Solver mode</label>
                <select
                  id="solver_type"
                  className="input-premium mt-1 w-full text-sm"
                  value={solverType}
                  onChange={(e) => setSolverType(e.target.value as 'GA_ONLY' | 'HYBRID' | 'CP_SAT_ONLY')}
                >
                  <option value="GA_ONLY">Genetic Algorithm (GA)</option>
                  <option value="HYBRID">Hybrid (GA + CP-SAT)</option>
                  <option value="CP_SAT_ONLY">CP-SAT Only</option>
                </select>
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
              <div>
                <label htmlFor="ga_population" className="text-xs font-medium text-slate-600">Population</label>
                <input
                  id="ga_population"
                  type="number"
                  min={2}
                  className="input-premium mt-1 w-full text-sm"
                  value={gaPopulationSize}
                  onChange={(e) => setGaPopulationSize(Number(e.target.value))}
                  disabled={solverType === 'CP_SAT_ONLY'}
                />
              </div>
              <div>
                <label htmlFor="ga_generations" className="text-xs font-medium text-slate-600">Generations</label>
                <input
                  id="ga_generations"
                  type="number"
                  min={1}
                  className="input-premium mt-1 w-full text-sm"
                  value={gaGenerations}
                  onChange={(e) => setGaGenerations(Number(e.target.value))}
                  disabled={solverType === 'CP_SAT_ONLY'}
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
              <div>
                <label htmlFor="ga_cpsat_seconds" className="text-xs font-medium text-slate-600">CP-SAT repair seconds (hybrid)</label>
                <input
                  id="ga_cpsat_seconds"
                  type="number"
                  min={0.1}
                  step={0.1}
                  className="input-premium mt-1 w-full text-sm"
                  value={gaCpSatSeconds}
                  onChange={(e) => setGaCpSatSeconds(Number(e.target.value))}
                  disabled={solverType !== 'HYBRID'}
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
                <div className="mt-1 text-sm text-slate-700">
                  Run lifecycle:{' '}
                  <span className="font-semibold">
                    {String(lastSolverResult?.run_status ?? '').toUpperCase() === 'COMPLETED'
                      ? 'Completed'
                      : String(lastSolverResult?.run_status ?? '').toUpperCase() === 'FAILED'
                        ? 'Failed'
                        : 'Running'}
                  </span>
                </div>
                <div className="mt-1 text-sm text-slate-700">Entries written: {lastRun.entries_written}</div>
                <div className="mt-1 text-sm text-slate-700">Conflicts: {lastRun.conflicts.length}</div>
                {lastRun.run_name ? (
                  <div className="mt-1 text-sm text-slate-700">Run name: {lastRun.run_name}</div>
                ) : null}
                {lastRun.solver_type ? (
                  <div className="mt-1 text-sm text-slate-700">Solver type: {lastRun.solver_type}</div>
                ) : null}
                {involvesGA ? (
                  <div className="mt-1 text-sm text-slate-700">
                    Normalized score: {
                      formatScore(normalizeFitnessScore(
                        lastRun.best_fitness,
                        Array.isArray(lastSolverResult?.history_best) ? lastSolverResult?.history_best : [],
                      ))
                    }
                  </div>
                ) : null}
                {effectiveSolverType === 'HYBRID' ? (
                  <>
                    <div className="mt-1 text-sm text-slate-700">
                      CP-SAT Repair Applied:{' '}
                      <span className="font-semibold">{(lastRun.cp_sat_repair_applied ?? true) ? 'YES' : 'NO'}</span>
                    </div>
                    <div className="mt-1 text-sm text-slate-700">
                      All Hard Constraints Satisfied:{' '}
                      <span className="font-semibold">{(lastRun.hard_constraints_satisfied ?? false) ? 'TRUE' : 'FALSE'}</span>
                    </div>
                  </>
                ) : null}
                {effectiveSolverType === 'CP_SAT_ONLY' ? (
                  <div className="mt-3 rounded-2xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-slate-800">
                    Constraint satisfaction summary: {' '}
                    <span className="font-semibold">
                      {lastRun.hard_constraints_satisfied ?? (lastRun.status === 'OPTIMAL' || lastRun.status === 'FEASIBLE' || lastRun.status === 'SUBOPTIMAL')
                        ? 'Hard constraints satisfied'
                        : 'Could not satisfy hard constraints'}
                    </span>
                  </div>
                ) : null}

                {lastRunEntries.length > 0 && slots.length > 0 ? (
                  <>
                    <TimetableModeTabs entries={lastRunEntries} />
                  </>
                ) : null}

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
                  {involvesGA ? (
                    <button
                      type="button"
                      className="btn-secondary text-sm font-medium text-slate-800"
                      onClick={() => setAnalyticsOpen(true)}
                    >
                      View GA Insights
                    </button>
                  ) : (
                    <button
                      type="button"
                      disabled
                      className="btn-secondary cursor-not-allowed text-sm font-medium text-slate-400"
                      title="Analytics are available only for GA_ONLY or HYBRID runs"
                    >
                      View GA Insights
                    </button>
                  )}
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

            {lastRun && involvesGA ? (
              <AnalyticsModal
                open={analyticsOpen}
                onClose={() => setAnalyticsOpen(false)}
                runId={lastRun.run_id}
                programCode={programCode}
                academicYearNumber={Number(academicYearNumber)}
                bestFitness={lastRun.best_fitness}
                generationCount={lastRun.generation_count}
                solverResult={lastSolverResult}
                entries={lastRunEntries}
                slots={slots}
              />
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

function AnalyticsModal({
  open,
  onClose,
  runId,
  programCode,
  academicYearNumber,
  bestFitness,
  generationCount,
  solverResult,
  entries,
  slots,
}: {
  open: boolean
  onClose: () => void
  runId: string
  programCode: string
  academicYearNumber: number
  bestFitness?: number | null
  generationCount?: number | null
  solverResult: Record<string, any> | null
  entries: TimetableEntry[]
  slots: TimeSlot[]
}) {
  const [tab, setTab] = React.useState<'overview' | 'fitness' | 'maps' | 'config'>('overview')
  const [compareRows, setCompareRows] = React.useState<Array<{
    id: string
    status: string
    normalized: number | null
    bestFitness: number | null
    hardConflicts: number
    penalty: number
  }>>([])
  const [compareLoading, setCompareLoading] = React.useState(false)
  const modalRef = React.useRef<HTMLDivElement | null>(null)
  const closeBtnRef = React.useRef<HTMLButtonElement | null>(null)
  const tabs: Array<'overview' | 'fitness' | 'maps' | 'config'> = ['overview', 'fitness', 'maps', 'config']

  React.useEffect(() => {
    if (!open) setTab('overview')
  }, [open])

  React.useEffect(() => {
    if (!open) return
    let cancelled = false

    ;(async () => {
      setCompareLoading(true)
      try {
        const runs = await listRuns({
          program_code: programCode,
          academic_year_number: academicYearNumber,
          limit: 20,
        })

        const rows = runs
          .map((r) => {
            const p = (r as any).parameters ?? {}
            const sr = p._solver_result ?? {}
            const historyBest: number[] = Array.isArray(sr.history_best) ? sr.history_best : []
            const breakdown = (sr.breakdown ?? {}) as Record<string, number>
            const hardConflicts =
              numOrZero(breakdown.teacher_conflicts) +
              numOrZero(breakdown.room_conflicts) +
              numOrZero(breakdown.class_conflicts) +
              numOrZero(breakdown.exclusive_room_violations)
            return {
              id: String(r.id),
              status: String(r.status),
              normalized: normalizeFitnessScore(sr.best_fitness, historyBest),
              bestFitness: Number.isFinite(Number(sr.best_fitness)) ? Number(sr.best_fitness) : null,
              hardConflicts,
              penalty: totalPenaltyFromBreakdown(breakdown),
            }
          })
          .filter((x) => x.bestFitness != null || x.hardConflicts > 0 || x.penalty > 0)
          .slice(0, 5)

        if (!cancelled) setCompareRows(rows)
      } catch {
        if (!cancelled) setCompareRows([])
      } finally {
        if (!cancelled) setCompareLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [open, programCode, academicYearNumber, runId])

  React.useEffect(() => {
    if (!open) return

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()

      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        const idx = tabs.indexOf(tab)
        const next = e.key === 'ArrowRight'
          ? tabs[(idx + 1) % tabs.length]
          : tabs[(idx - 1 + tabs.length) % tabs.length]
        setTab(next)
        e.preventDefault()
      }

      if (e.key === 'Tab' && modalRef.current) {
        const focusable = Array.from(
          modalRef.current.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
          ),
        ).filter((el) => !el.hasAttribute('disabled') && el.offsetParent !== null)

        if (focusable.length === 0) return

        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        const active = document.activeElement as HTMLElement | null

        if (!e.shiftKey && active === last) {
          first.focus()
          e.preventDefault()
        } else if (e.shiftKey && active === first) {
          last.focus()
          e.preventDefault()
        }
      }
    }

    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKeyDown)
    window.setTimeout(() => closeBtnRef.current?.focus(), 0)

    return () => {
      document.body.style.overflow = prevOverflow
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [open, onClose, tab])

  const bestRunId = React.useMemo(() => {
    if (!compareRows.length) return ''
    const scored = compareRows
      .filter((r) => r.normalized != null)
      .sort((a, b) => (b.normalized ?? -1) - (a.normalized ?? -1) || a.penalty - b.penalty)
    return scored[0]?.id ?? ''
  }, [compareRows])

  const exportComparisonCsv = React.useCallback(() => {
    if (!compareRows.length) return
    const header = ['run_id', 'status', 'normalized_score', 'best_fitness', 'hard_conflicts', 'penalty']
    const rows = compareRows.map((r) => [
      r.id,
      r.status,
      formatScore(r.normalized),
      r.bestFitness == null ? '-' : r.bestFitness.toFixed(1),
      String(r.hardConflicts),
      String(r.penalty),
    ])
    const csv = [header, ...rows]
      .map((line) => line.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `ga_run_comparison_${programCode}_y${academicYearNumber}.csv`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, [compareRows, programCode, academicYearNumber])

  if (!open) return null

  const historyBest: number[] = Array.isArray(solverResult?.history_best) ? solverResult!.history_best : []
  const historyMean: number[] = Array.isArray(solverResult?.history_mean) ? solverResult!.history_mean : []
  const breakdown = (solverResult?.breakdown ?? {}) as Record<string, number>
  const averageFitness = historyMean.length > 0 ? historyMean[historyMean.length - 1] : null
  const normalizedScore = normalizeFitnessScore(bestFitness, historyBest)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-label="GA Insights"
        className="flex h-[90vh] w-full max-w-7xl flex-col overflow-hidden rounded-2xl border bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 z-20 flex items-center justify-between border-b bg-white px-5 py-4">
          <div>
            <div className="text-base font-semibold text-slate-900">GA Insights</div>
            <div className="text-xs text-slate-500">Analytics separated from main timetable view</div>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            className="btn-secondary text-sm font-medium text-slate-800"
            onClick={onClose}
          >
            Close
          </button>
        </div>

        <div className="sticky top-[73px] z-10 border-b bg-white px-4 py-2">
          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Analytics sections">
            {([
              ['overview', 'Overview'],
              ['fitness', 'Fitness'],
              ['maps', 'Maps'],
              ['config', 'Config'],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                className={
                  'rounded-lg px-3 py-1 text-sm font-medium ' +
                  (tab === value ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700')
                }
                onClick={() => setTab(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <div className="sticky top-0 z-10 mb-4 rounded-xl border bg-white/95 p-3 backdrop-blur">
            <div className="grid gap-2 text-xs text-slate-600 md:grid-cols-4">
              <div>Best: <span className="font-semibold text-slate-900">{bestFitness == null ? '-' : bestFitness.toFixed(2)}</span></div>
              <div>Avg: <span className="font-semibold text-slate-900">{averageFitness == null ? '-' : averageFitness.toFixed(2)}</span></div>
              <div>Generations: <span className="font-semibold text-slate-900">{generationCount ?? historyBest.length}</span></div>
              <div>Normalized: <span className="font-semibold text-slate-900">{formatScore(normalizedScore)}</span></div>
            </div>
          </div>

          {tab === 'overview' ? (
            <div className="space-y-4">
              <div className="grid gap-2 md:grid-cols-3">
                <StatCard label="Best Fitness" value={bestFitness} />
                <StatCard label="Average Fitness" value={averageFitness} />
                <StatCard label="Generation Count" value={generationCount} />
              </div>
              <div className="rounded-xl border bg-indigo-50 px-3 py-2 text-sm text-indigo-900">
                Normalized score (0-100): <span className="font-semibold">{formatScore(normalizedScore)}</span>
              </div>

              <div className="rounded-xl border bg-slate-50 p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-semibold text-slate-700">Run Comparison (Last 5)</div>
                  <button
                    type="button"
                    onClick={exportComparisonCsv}
                    disabled={compareRows.length === 0}
                    className="btn-secondary text-xs font-medium text-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Export CSV
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-slate-600">
                  <span className="inline-flex items-center gap-1">
                    <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
                    Good (&gt;= 70)
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
                    Needs Improvement (40-69.9)
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <span className="h-2.5 w-2.5 rounded-full bg-rose-400" />
                    Poor (&lt; 40)
                  </span>
                </div>
                {compareLoading ? (
                  <div className="mt-2 text-xs text-slate-500">Loading recent runs…</div>
                ) : compareRows.length === 0 ? (
                  <div className="mt-2 text-xs text-slate-500">No comparable GA/HYBRID runs found.</div>
                ) : (
                  <div className="mt-2 overflow-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b text-left text-slate-500">
                          <th className="py-1">Run</th>
                          <th className="py-1">Status</th>
                          <th className="py-1">Normalized</th>
                          <th className="py-1">Best Fitness</th>
                          <th className="py-1">Hard Conflicts</th>
                          <th className="py-1">Penalty</th>
                        </tr>
                      </thead>
                      <tbody>
                        {compareRows.map((row) => (
                          <tr
                            key={row.id}
                            className={
                              'border-b last:border-0 ' +
                              (row.normalized == null
                                ? ''
                                : row.normalized >= 70
                                  ? 'bg-emerald-50'
                                  : row.normalized >= 40
                                    ? 'bg-amber-50'
                                    : 'bg-rose-50')
                            }
                          >
                            <td className="py-1 text-slate-700">
                              <span>{shortRunId(row.id)}</span>
                              {row.id === bestRunId ? (
                                <span className="ml-2 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                                  BEST
                                </span>
                              ) : null}
                            </td>
                            <td className="py-1 text-slate-700">{row.status}</td>
                            <td className="py-1 text-slate-700">{formatScore(row.normalized)}</td>
                            <td className="py-1 text-slate-700">{row.bestFitness == null ? '-' : row.bestFitness.toFixed(1)}</td>
                            <td className="py-1 text-slate-700">{row.hardConflicts}</td>
                            <td className="py-1 text-slate-700">{row.penalty}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div className="rounded-xl border bg-slate-50 p-3">
                <div className="text-xs font-semibold text-slate-700">Penalty Breakdown</div>
                <div className="mt-2 grid gap-2 text-sm text-slate-700 sm:grid-cols-2 lg:grid-cols-3">
                  <div>Teacher Conflicts: <span className="font-semibold">{numOrZero(breakdown.teacher_conflicts)}</span></div>
                  <div>Room Conflicts: <span className="font-semibold">{numOrZero(breakdown.room_conflicts)}</span></div>
                  <div>Class Conflicts: <span className="font-semibold">{numOrZero(breakdown.class_conflicts)}</span></div>
                  <div>Exclusive Room Violations: <span className="font-semibold">{numOrZero(breakdown.exclusive_room_violations)}</span></div>
                  <div>Missing Lectures: <span className="font-semibold">{numOrZero(breakdown.missing_min_lectures)}</span></div>
                  <div>Teacher Overload: <span className="font-semibold">{numOrZero(breakdown.teacher_overload)}</span></div>
                </div>
              </div>
            </div>
          ) : null}

          {tab === 'fitness' ? (
            <div className="space-y-4">
              <div className="rounded-xl border bg-slate-50 p-3">
                <div className="text-xs font-semibold text-slate-700">Fitness Graph</div>
                <FitnessGraph best={historyBest} mean={historyMean} />
              </div>
              {historyBest.length > 0 ? (
                <details className="rounded-xl border bg-slate-50 p-3" open>
                  <summary className="cursor-pointer text-sm font-semibold text-slate-900">Evolution Logs</summary>
                  <div className="mt-2 max-h-[320px] overflow-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b text-left text-xs text-slate-500">
                          <th className="py-1">Generation</th>
                          <th className="py-1">Best</th>
                          <th className="py-1">Average</th>
                        </tr>
                      </thead>
                      <tbody>
                        {historyBest.map((v, i) => (
                          <tr key={i} className="border-b last:border-0">
                            <td className="py-1 text-slate-700">{i + 1}</td>
                            <td className="py-1 text-slate-700">{v.toFixed(2)}</td>
                            <td className="py-1 text-slate-700">{(historyMean[i] ?? 0).toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              ) : null}
            </div>
          ) : null}

          {tab === 'maps' ? (
            entries.length > 0 && slots.length > 0 ? (
              <div className="rounded-xl border bg-slate-50 p-2">
                <ConstraintMapPanel
                  entries={entries}
                  slots={slots}
                  rawTimetableData={Array.isArray(solverResult?.timetable_data) ? solverResult?.timetable_data : []}
                />
              </div>
            ) : (
              <div className="rounded-xl border bg-slate-50 p-3 text-sm text-slate-600">
                No timetable data available yet for constraint maps.
              </div>
            )
          ) : null}

          {tab === 'config' ? (
            <div className="rounded-xl border bg-slate-50 p-3 text-sm text-slate-700">
              <div className="text-xs font-semibold text-slate-700">GA Configuration</div>
              <div className="mt-2">Selection: Tournament (k=5)</div>
              <div>Crossover: Day-block + Class-block</div>
              <div>Mutation: Slot swap, Room reassignment, Day move</div>
              <div>Elitism: Enabled (Top 2)</div>
              <div className="mt-1 text-xs text-slate-500">Generations: {generationCount ?? historyBest.length}</div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function GAAnalyticsPanel({
  bestFitness,
  generationCount,
  solverResult,
}: {
  bestFitness?: number | null
  generationCount?: number | null
  solverResult: Record<string, any> | null
}) {
  const historyBest: number[] = Array.isArray(solverResult?.history_best) ? solverResult!.history_best : []
  const historyMean: number[] = Array.isArray(solverResult?.history_mean) ? solverResult!.history_mean : []
  const breakdown = (solverResult?.breakdown ?? {}) as Record<string, number>
  const averageFitness = historyMean.length > 0 ? historyMean[historyMean.length - 1] : null
  const worstFitness = historyBest.length > 0 ? Math.min(...historyBest) : null

  return (
    <div className="mt-3 rounded-2xl border bg-white p-4">
      <div className="text-sm font-semibold text-slate-900">GA Analytics</div>

      <div className="mt-3 grid gap-2 md:grid-cols-3">
        <StatCard label="Best Fitness" value={bestFitness} />
        <StatCard label="Average Fitness" value={averageFitness} />
        <StatCard label="Worst Fitness" value={worstFitness} />
      </div>

      <div className="mt-3 rounded-xl border bg-slate-50 p-3">
        <div className="text-xs font-semibold text-slate-700">Penalty Breakdown</div>
        <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3 text-sm text-slate-700">
          <div>Teacher Conflicts: <span className="font-semibold">{numOrZero(breakdown.teacher_conflicts)}</span></div>
          <div>Room Conflicts: <span className="font-semibold">{numOrZero(breakdown.room_conflicts)}</span></div>
          <div>Class Conflicts: <span className="font-semibold">{numOrZero(breakdown.class_conflicts)}</span></div>
          <div>Exclusive Room Violations: <span className="font-semibold">{numOrZero(breakdown.exclusive_room_violations)}</span></div>
          <div>Missing Lectures: <span className="font-semibold">{numOrZero(breakdown.missing_min_lectures)}</span></div>
          <div>Teacher Overload: <span className="font-semibold">{numOrZero(breakdown.teacher_overload)}</span></div>
        </div>
      </div>

      <div className="mt-3 rounded-xl border bg-slate-50 p-3">
        <div className="text-xs font-semibold text-slate-700">Fitness Graph</div>
        <FitnessGraph best={historyBest} mean={historyMean} />
      </div>

      <div className="mt-3 rounded-xl border bg-slate-50 p-3 text-sm text-slate-700">
        <div className="text-xs font-semibold text-slate-700">GA Configuration</div>
        <div className="mt-2">Selection: Tournament (k=5)</div>
        <div>Crossover: Day-block + Class-block</div>
        <div>Mutation: Slot swap, Room reassignment, Day move</div>
        <div>Elitism: Enabled (Top 2)</div>
        <div className="mt-1 text-xs text-slate-500">Generations: {generationCount ?? historyBest.length}</div>
      </div>

      {historyBest.length > 0 ? (
        <details className="mt-3 rounded-xl border bg-slate-50 p-3">
          <summary className="cursor-pointer text-sm font-semibold text-slate-900">Evolution Logs</summary>
          <div className="mt-2 max-h-[220px] overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-slate-500">
                  <th className="py-1">Generation</th>
                  <th className="py-1">Best</th>
                  <th className="py-1">Average</th>
                </tr>
              </thead>
              <tbody>
                {historyBest.map((v, i) => (
                  <tr key={i} className="border-b last:border-0">
                    <td className="py-1 text-slate-700">{i + 1}</td>
                    <td className="py-1 text-slate-700">{v.toFixed(2)}</td>
                    <td className="py-1 text-slate-700">{(historyMean[i] ?? 0).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="rounded-xl border bg-slate-50 p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-slate-900">{value == null ? '-' : value.toFixed(2)}</div>
    </div>
  )
}

function FitnessGraph({ best, mean }: { best: number[]; mean: number[] }) {
  if (best.length === 0) {
    return <div className="mt-2 text-xs text-slate-500">No GA generation history available.</div>
  }

  const width = 740
  const height = 220
  const pad = 24
  const points = Math.max(1, best.length - 1)
  const all = [...best, ...(mean.length ? mean : best)]
  const minY = Math.min(...all)
  const maxY = Math.max(...all)
  const span = Math.max(1, maxY - minY)

  const line = (arr: number[]) =>
    arr
      .map((v, i) => {
        const x = pad + ((width - 2 * pad) * i) / points
        const y = height - pad - ((height - 2 * pad) * (v - minY)) / span
        return `${x.toFixed(2)},${y.toFixed(2)}`
      })
      .join(' ')

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="mt-2 h-52 w-full rounded-lg border bg-white">
      <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} stroke="#94a3b8" />
      <line x1={pad} y1={pad} x2={pad} y2={height - pad} stroke="#94a3b8" />
      <polyline points={line(best)} fill="none" stroke="#0f766e" strokeWidth="2.5" />
      <polyline points={line(mean)} fill="none" stroke="#0284c7" strokeWidth="2" />
      <text x={pad + 8} y={pad + 12} fontSize="12" fill="#0f766e">Best Fitness</text>
      <text x={pad + 108} y={pad + 12} fontSize="12" fill="#0284c7">Average Fitness</text>
    </svg>
  )
}

function ConstraintMapPanel({
  entries,
  slots,
  rawTimetableData,
}: {
  entries: TimetableEntry[]
  slots: TimeSlot[]
  rawTimetableData?: Array<Record<string, any>>
}) {
  const days = Array.from(new Set(slots.map((s) => s.day_of_week))).sort((a, b) => a - b)
  const periods = Array.from(new Set(slots.map((s) => s.slot_index))).sort((a, b) => a - b)
  const dayLabel = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

  const rawEvents = React.useMemo(() => {
    if (!Array.isArray(rawTimetableData) || rawTimetableData.length === 0) return []
    return rawTimetableData
      .map((g) => ({
        day: Number(g.day),
        period: Number(g.period),
        teacher_id: String(g.teacher_id ?? ''),
        room_id: String(g.room_id ?? ''),
        section_id: String(g.class_id ?? ''),
      }))
      .filter(
        (g) => Number.isFinite(g.day) && Number.isFinite(g.period) && g.teacher_id && g.room_id && g.section_id,
      )
  }, [rawTimetableData])

  const mapEvents = React.useMemo(() => {
    if (rawEvents.length > 0) return rawEvents
    return entries.map((e) => ({
      day: e.day_of_week,
      period: e.slot_index,
      teacher_id: e.teacher_id,
      room_id: e.room_id,
      section_id: e.section_id,
    }))
  }, [entries, rawEvents])

  const usesRawMapData = rawEvents.length > 0

  const slotStats = React.useMemo(() => {
    const map = new Map<string, { entries: number; conflicts: number }>()
    const push = (k: string, entriesCount = 0, conflicts = 0) => {
      const old = map.get(k) ?? { entries: 0, conflicts: 0 }
      map.set(k, { entries: old.entries + entriesCount, conflicts: old.conflicts + conflicts })
    }

    for (const d of days) {
      for (const p of periods) {
        push(`${d}:${p}`, 0, 0)
      }
    }

    const bySlot = new Map<string, Array<{ teacher_id: string; room_id: string; section_id: string }>>()
    for (const e of mapEvents) {
      const key = `${e.day}:${e.period}`
      const arr = bySlot.get(key) ?? []
      arr.push(e)
      bySlot.set(key, arr)
    }

    for (const [k, arr] of bySlot.entries()) {
      const teacher = new Map<string, number>()
      const room = new Map<string, number>()
      const section = new Map<string, number>()
      for (const e of arr) {
        teacher.set(e.teacher_id, (teacher.get(e.teacher_id) ?? 0) + 1)
        room.set(e.room_id, (room.get(e.room_id) ?? 0) + 1)
        section.set(e.section_id, (section.get(e.section_id) ?? 0) + 1)
      }
      const conflicts =
        [...teacher.values(), ...room.values(), ...section.values()]
          .filter((v) => v > 1)
          .reduce((s, v) => s + (v - 1), 0)
      map.set(k, { entries: arr.length, conflicts })
    }

    return map
  }, [days, mapEvents, periods])

  const aggregateMap = (kind: 'teacher' | 'room' | 'class') => {
    const m = new Map<string, number>()
    for (const d of days) {
      for (const p of periods) {
        m.set(`${d}:${p}`, 0)
      }
    }
    const seen = new Map<string, Set<string>>()
    for (const e of mapEvents) {
      const k = `${e.day}:${e.period}`
      const v = kind === 'teacher' ? e.teacher_id : kind === 'room' ? e.room_id : e.section_id
      const set = seen.get(k) ?? new Set<string>()
      set.add(v)
      seen.set(k, set)
    }
    for (const [k, set] of seen.entries()) {
      m.set(k, set.size)
    }
    return m
  }

  const teacherMap = React.useMemo(() => aggregateMap('teacher'), [days, mapEvents, periods])
  const roomMap = React.useMemo(() => aggregateMap('room'), [days, mapEvents, periods])
  const classMap = React.useMemo(() => aggregateMap('class'), [days, mapEvents, periods])

  return (
    <div className="mt-3 rounded-2xl border bg-white p-4">
      <div className="text-sm font-semibold text-slate-900">Constraint Map</div>
      <div className="mt-2 text-xs text-slate-500">Red = conflict, green = valid occupied slot.</div>
      {usesRawMapData ? (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Showing raw GA chromosome conflicts (before persistence cleanup).
        </div>
      ) : null}
      <HeatmapGrid
        title="Conflict Heatmap"
        days={days}
        periods={periods}
        dayLabel={dayLabel}
        valueFor={(d, p) => slotStats.get(`${d}:${p}`)?.entries ?? 0}
        toneFor={(d, p) => {
          const cell = slotStats.get(`${d}:${p}`)
          if (!cell || cell.entries === 0) return 'none'
          return cell.conflicts > 0 ? 'bad' : 'good'
        }}
        labelFor={(d, p) => {
          const cell = slotStats.get(`${d}:${p}`)
          if (!cell || cell.entries === 0) return '-'
          return cell.conflicts > 0 ? `C:${cell.conflicts}` : `E:${cell.entries}`
        }}
      />

      <div className="mt-3 grid gap-3 md:grid-cols-3">
        <HeatmapGrid
          title="Teacher Schedule Map"
          compact
          days={days}
          periods={periods}
          dayLabel={dayLabel}
          valueFor={(d, p) => teacherMap.get(`${d}:${p}`) ?? 0}
          toneFor={(d, p) => ((teacherMap.get(`${d}:${p}`) ?? 0) > 0 ? 'good' : 'none')}
          labelFor={(d, p) => String(teacherMap.get(`${d}:${p}`) ?? 0)}
        />
        <HeatmapGrid
          title="Room Occupancy Map"
          compact
          days={days}
          periods={periods}
          dayLabel={dayLabel}
          valueFor={(d, p) => roomMap.get(`${d}:${p}`) ?? 0}
          toneFor={(d, p) => ((roomMap.get(`${d}:${p}`) ?? 0) > 0 ? 'good' : 'none')}
          labelFor={(d, p) => String(roomMap.get(`${d}:${p}`) ?? 0)}
        />
        <HeatmapGrid
          title="Class Timetable Map"
          compact
          days={days}
          periods={periods}
          dayLabel={dayLabel}
          valueFor={(d, p) => classMap.get(`${d}:${p}`) ?? 0}
          toneFor={(d, p) => ((classMap.get(`${d}:${p}`) ?? 0) > 0 ? 'good' : 'none')}
          labelFor={(d, p) => String(classMap.get(`${d}:${p}`) ?? 0)}
        />
      </div>
    </div>
  )
}

function HeatmapGrid({
  title,
  days,
  periods,
  dayLabel,
  valueFor,
  toneFor,
  labelFor,
  compact,
}: {
  title: string
  days: number[]
  periods: number[]
  dayLabel: string[]
  valueFor: (day: number, period: number) => number
  toneFor: (day: number, period: number) => 'good' | 'bad' | 'none'
  labelFor: (day: number, period: number) => string
  compact?: boolean
}) {
  return (
    <div className="rounded-xl border bg-slate-50 p-3">
      <div className="mb-2 text-xs font-semibold text-slate-700">{title}</div>
      <div className="overflow-auto">
        <table className="w-full text-xs">
          <thead>
            <tr>
              <th className="px-1 py-1 text-left text-slate-500">P\D</th>
              {days.map((d) => (
                <th key={d} className="px-1 py-1 text-slate-500">{dayLabel[d] ?? `D${d + 1}`}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {periods.map((p) => (
              <tr key={p}>
                <td className="px-1 py-1 text-slate-500">{p + 1}</td>
                {days.map((d) => {
                  const tone = toneFor(d, p)
                  const value = valueFor(d, p)
                  const cls =
                    tone === 'bad'
                      ? 'bg-rose-200 text-rose-900'
                      : tone === 'good'
                        ? 'bg-emerald-200 text-emerald-900'
                        : 'bg-slate-100 text-slate-500'
                  return (
                    <td key={`${d}:${p}`} className="px-1 py-1">
                      <div className={`rounded px-1 py-1 text-center ${compact ? 'text-[10px]' : 'text-xs'} ${cls}`}>
                        {value > 0 ? labelFor(d, p) : '-'}
                      </div>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function TimetableModeTabs({ entries }: { entries: TimetableEntry[] }) {
  const [tab, setTab] = React.useState<'class' | 'teacher' | 'room'>('class')
  const [selected, setSelected] = React.useState<string>('')

  const options = React.useMemo(() => {
    const map = new Map<string, string>()
    for (const e of entries) {
      if (tab === 'class') map.set(e.section_id, e.section_code)
      if (tab === 'teacher') map.set(e.teacher_id, e.teacher_code)
      if (tab === 'room') map.set(e.room_id, e.room_code)
    }
    return Array.from(map.entries()).map(([id, code]) => ({ id, code }))
  }, [entries, tab])

  React.useEffect(() => {
    if (!selected || !options.some((o) => o.id === selected)) {
      setSelected(options[0]?.id ?? '')
    }
  }, [options, selected])

  const filtered = React.useMemo(() => {
    return entries.filter((e) => {
      if (tab === 'class') return e.section_id === selected
      if (tab === 'teacher') return e.teacher_id === selected
      return e.room_id === selected
    })
  }, [entries, selected, tab])

  const keyOf = (e: TimetableEntry) => `${e.day_of_week}:${e.slot_index}`
  const byCell = new Map<string, TimetableEntry[]>()
  for (const e of filtered) {
    const k = keyOf(e)
    const arr = byCell.get(k) ?? []
    arr.push(e)
    byCell.set(k, arr)
  }

  const days = Array.from(new Set(entries.map((s) => s.day_of_week))).sort((a, b) => a - b)
  const periods = Array.from(new Set(entries.map((s) => s.slot_index))).sort((a, b) => a - b)
  const dayLabel = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

  return (
    <div className="mt-3 rounded-2xl border bg-white p-4">
      <div className="text-sm font-semibold text-slate-900">Timetable View</div>
      <div className="mt-2 flex flex-wrap gap-2">
        {(['class', 'teacher', 'room'] as const).map((t) => (
          <button
            key={t}
            className={
              'rounded-lg px-3 py-1 text-sm font-medium ' +
              (tab === t ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700')
            }
            onClick={() => setTab(t)}
            type="button"
          >
            {t === 'class' ? 'Class View' : t === 'teacher' ? 'Teacher View' : 'Room View'}
          </button>
        ))}
      </div>

      <div className="mt-2">
        <select
          aria-label="Timetable entity selector"
          className="input-premium w-full text-sm"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          {options.map((o) => (
            <option key={o.id} value={o.id}>{o.code}</option>
          ))}
        </select>
      </div>

      <div className="mt-3 overflow-auto rounded-xl border">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-slate-50">
              <th className="px-2 py-2 text-left text-slate-500">Period / Day</th>
              {days.map((d) => (
                <th key={d} className="px-2 py-2 text-left text-slate-500">{dayLabel[d] ?? `D${d + 1}`}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {periods.map((p) => (
              <tr key={p} className="border-t">
                <td className="px-2 py-2 text-slate-600">P{p + 1}</td>
                {days.map((d) => {
                  const arr = byCell.get(`${d}:${p}`) ?? []
                  return (
                    <td key={`${d}:${p}`} className="px-2 py-2 align-top">
                      {arr.length === 0 ? (
                        <span className="text-slate-400">-</span>
                      ) : (
                        <div className="space-y-1">
                          {arr.map((e) => (
                            <div key={e.id} className="rounded border bg-slate-50 px-2 py-1">
                              <div className="font-semibold text-slate-900">{e.subject_code}</div>
                              <div className="text-slate-700">{e.teacher_code}</div>
                              <div className="text-slate-700">{e.room_code}</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function numOrZero(v: unknown): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function normalizeFitnessScore(fitness: unknown, historyBest: number[] = []): number | null {
  const f = Number(fitness)
  if (!Number.isFinite(f)) return null

  const finiteHistory = (historyBest || []).map((x) => Number(x)).filter((x) => Number.isFinite(x))
  if (finiteHistory.length >= 2) {
    const minV = Math.min(...finiteHistory)
    const maxV = Math.max(...finiteHistory)
    const span = maxV - minV
    if (span > 0) {
      const pct = ((f - minV) / span) * 100
      return Math.max(0, Math.min(100, pct))
    }
  }

  // Fallback: smooth absolute normalization when history is flat or unavailable.
  const pct = 100 / (1 + Math.exp(-f / 10000))
  return Math.max(0, Math.min(100, pct))
}

function formatScore(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '-'
  return `${v.toFixed(1)}`
}

function shortRunId(id: string): string {
  if (!id) return '-'
  return `${id.slice(0, 8)}...`
}

function totalPenaltyFromBreakdown(b: Record<string, number>): number {
  return (
    1000 * numOrZero(b.teacher_conflicts) +
    1000 * numOrZero(b.room_conflicts) +
    1000 * numOrZero(b.class_conflicts) +
    1500 * numOrZero(b.exclusive_room_violations) +
    500 * numOrZero(b.missing_min_lectures) +
    300 * numOrZero(b.teacher_overload) +
    100 * numOrZero(b.uneven_distribution) +
    50 * numOrZero(b.schedule_gaps)
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
