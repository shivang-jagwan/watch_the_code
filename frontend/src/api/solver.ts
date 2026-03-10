import { apiFetch } from './client'

export type RunSummary = {
  id: string
  created_at: string
  status: string
  solver_version?: string | null
  seed?: number | null
  parameters: Record<string, any>
  notes?: string | null
}

export type RunDetail = RunSummary & {
  conflicts_total: number
  entries_total: number
}

export type SolverConflict = {
  severity: 'INFO' | 'WARN' | 'ERROR'
  id?: string | null
  conflict_type: string
  message: string
  section_id?: string | null
  teacher_id?: string | null
  subject_id?: string | null
  room_id?: string | null
  slot_id?: string | null
  details?: Record<string, any>
  metadata: Record<string, any>
}

export type GenerateTimetableResponse = {
  run_id: string
  status: 'FAILED_VALIDATION' | 'READY_FOR_SOLVE'
  conflicts: SolverConflict[]
}

export type SolveTimetableResponse = {
  run_id: string
  status: 'RUNNING' | 'FAILED_VALIDATION' | 'INFEASIBLE' | 'FEASIBLE' | 'SUBOPTIMAL' | 'OPTIMAL' | 'ERROR'
  entries_written: number
  conflicts: SolverConflict[]

  reason_summary?: string | null
  diagnostics?: Record<string, any>[]
  objective_score?: number | null
  improvements_possible?: boolean | null
  warnings?: string[]
  soft_conflicts?: SolverConflict[]
  solver_stats?: Record<string, any>

  // time-budget fields
  best_bound?: number | null
  optimality_gap?: number | null
  solve_time_seconds?: number | null
  message?: string | null
}

/** Poll /runs/{runId} every `intervalMs` ms until the run finishes (non-RUNNING status). */
export async function pollRunUntilDone(
  runId: string,
  onTick: (detail: RunDetail) => void,
  intervalMs = 4000,
  timeoutMs = 360_000,
): Promise<RunDetail> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, intervalMs))
    const detail = await getRun(runId)
    onTick(detail)
    if (detail.status !== 'RUNNING' && detail.status !== 'CREATED') return detail
  }
  throw new Error('Poll timeout: solver did not complete within the allowed time.')
}

export type SolveTimetableRequest = {
  program_code: string
  academic_year_number: number
  seed?: number | null
  max_time_seconds?: number
  relax_teacher_load_limits?: boolean
  require_optimal?: boolean
}

export type SolveGlobalTimetableRequest = {
  program_code: string
  seed?: number | null
  max_time_seconds?: number
  relax_teacher_load_limits?: boolean
  require_optimal?: boolean
}

export async function generateTimetable(payload: {
  program_code: string
  academic_year_number: number
  seed?: number | null
}): Promise<GenerateTimetableResponse> {
  return apiFetch<GenerateTimetableResponse>('/api/solver/generate', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function generateTimetableGlobal(payload: {
  program_code: string
  seed?: number | null
}): Promise<GenerateTimetableResponse> {
  return apiFetch<GenerateTimetableResponse>('/api/solver/generate-global', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function solveTimetable(payload: SolveTimetableRequest): Promise<SolveTimetableResponse> {
  return apiFetch<SolveTimetableResponse>('/api/solver/solve', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function solveTimetableGlobal(payload: SolveGlobalTimetableRequest): Promise<SolveTimetableResponse> {
  return apiFetch<SolveTimetableResponse>('/api/solver/solve-global', {
    method: 'POST',
    body: JSON.stringify({
      program_code: payload.program_code,
      seed: payload.seed ?? null,
      max_time_seconds: payload.max_time_seconds ?? 300,
      relax_teacher_load_limits: Boolean(payload.relax_teacher_load_limits),
      require_optimal: Boolean(payload.require_optimal),
    }),
  })
}

export type TimetableEntry = {
  id: string
  run_id: string

  section_id: string
  section_code: string
  section_name: string

  subject_id: string
  subject_code: string
  subject_name: string
  subject_type: string

  teacher_id: string
  teacher_code: string
  teacher_name: string

  room_id: string
  room_code: string
  room_name: string
  room_type: string

  slot_id: string
  day_of_week: number
  slot_index: number
  start_time: string
  end_time: string

  combined_class_id?: string | null
  elective_block_id?: string | null
  elective_block_name?: string | null
  created_at: string
}

export type TimeSlot = {
  id: string
  day_of_week: number
  slot_index: number
  start_time: string
  end_time: string
  is_lunch_break?: boolean
}

export async function listRuns(params?: {
  program_code?: string
  academic_year_number?: number
  limit?: number
}): Promise<RunSummary[]> {
  const qs = new URLSearchParams()
  if (params?.program_code) qs.set('program_code', params.program_code)
  if (params?.academic_year_number != null) qs.set('academic_year_number', String(params.academic_year_number))
  if (params?.limit != null) qs.set('limit', String(params.limit))
  const path = `/api/solver/runs${qs.toString() ? `?${qs.toString()}` : ''}`
  const data = await apiFetch<{ runs: RunSummary[] }>(path)
  return data.runs
}

export async function getRun(runId: string): Promise<RunDetail> {
  return apiFetch<RunDetail>(`/api/solver/runs/${runId}`)
}

export async function listRunConflicts(runId: string): Promise<SolverConflict[]> {
  const data = await apiFetch<{ run_id: string; conflicts: SolverConflict[] }>(
    `/api/solver/runs/${runId}/conflicts`,
  )
  return data.conflicts
}

// ── Timetable Validation ──────────────────────────────────────────────────

export type ValidationIssue = {
  type: string
  resource?: string | null
  resource_type?: string | null
  teacher_id?: string | null
  teacher?: string | null
  section_id?: string | null
  section?: string | null
  subject_id?: string | null
  subject?: string | null
  required?: number | null
  capacity?: number | null
  shortage?: number | null
  contributors?: Record<string, any>[]
  suggestion?: string | null
}

export type ValidateTimetableResponse = {
  status: 'VALID' | 'WARNINGS' | 'INVALID'
  errors: SolverConflict[]
  warnings: SolverConflict[]
  capacity_issues: ValidationIssue[]
  summary: Record<string, any>
}

export async function validateTimetable(payload: {
  program_code: string
  academic_year_number?: number | null
}): Promise<ValidateTimetableResponse> {
  return apiFetch<ValidateTimetableResponse>('/api/solver/validate', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function listRunEntries(
  runId: string,
  sectionCode?: string,
): Promise<TimetableEntry[]> {
  const qs = new URLSearchParams()
  if (sectionCode) qs.set('section_code', sectionCode)
  const path = `/api/solver/runs/${runId}/entries${qs.toString() ? `?${qs.toString()}` : ''}`
  const data = await apiFetch<{ run_id: string; entries: TimetableEntry[] }>(path)
  return data.entries
}

export async function listTimeSlots(): Promise<TimeSlot[]> {
  const data = await apiFetch<{ slots: TimeSlot[] }>(`/api/solver/time-slots`)
  return data.slots
}

export type FixedTimetableEntry = {
  id: string

  section_id: string
  section_code: string
  section_name: string

  subject_id: string
  subject_code: string
  subject_name: string
  subject_type: string

  teacher_id: string
  teacher_code: string
  teacher_name: string

  room_id: string
  room_code: string
  room_name: string
  room_type: string

  slot_id: string
  day_of_week: number
  slot_index: number
  start_time: string
  end_time: string

  is_active: boolean
  created_at: string
}

export type SpecialAllotment = {
  id: string

  section_id: string
  section_code: string
  section_name: string

  subject_id: string
  subject_code: string
  subject_name: string
  subject_type: string

  teacher_id: string
  teacher_code: string
  teacher_name: string

  room_id: string
  room_code: string
  room_name: string
  room_type: string

  slot_id: string
  day_of_week: number
  slot_index: number
  start_time: string
  end_time: string

  reason?: string | null
  is_active: boolean
  created_at: string
}

export async function listFixedEntries(params: {
  section_id: string
  include_inactive?: boolean
}): Promise<FixedTimetableEntry[]> {
  const qs = new URLSearchParams({ section_id: params.section_id })
  if (params.include_inactive) qs.set('include_inactive', 'true')
  const data = await apiFetch<{ entries: FixedTimetableEntry[] }>(`/api/solver/fixed-entries?${qs.toString()}`)
  return data.entries
}

export async function upsertFixedEntry(payload: {
  section_id: string
  subject_id: string
  teacher_id: string
  room_id: string
  slot_id: string
}): Promise<FixedTimetableEntry> {
  return apiFetch<FixedTimetableEntry>(`/api/solver/fixed-entries`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteFixedEntry(entry_id: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>(`/api/solver/fixed-entries/${entry_id}`, { method: 'DELETE' })
}

export async function listSpecialAllotments(params: {
  section_id: string
  include_inactive?: boolean
}): Promise<SpecialAllotment[]> {
  const qs = new URLSearchParams({ section_id: params.section_id })
  if (params.include_inactive) qs.set('include_inactive', 'true')
  const data = await apiFetch<{ entries: SpecialAllotment[] }>(
    `/api/solver/special-allotments?${qs.toString()}`,
  )
  return data.entries
}

export async function upsertSpecialAllotment(payload: {
  section_id: string
  subject_id: string
  teacher_id: string
  room_id: string
  slot_id: string
  reason?: string | null
}): Promise<SpecialAllotment> {
  return apiFetch<SpecialAllotment>(`/api/solver/special-allotments`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteSpecialAllotment(entry_id: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>(`/api/solver/special-allotments/${entry_id}`, { method: 'DELETE' })
}

export type RequiredSubject = {
  id: string
  program_id: string
  academic_year_id: string
  code: string
  name: string
  subject_type: string
  sessions_per_week: number
  max_per_day: number
  lab_block_size_slots: number
  is_active: boolean
  created_at: string
}

export async function listSectionRequiredSubjects(params: {
  section_id: string
}): Promise<RequiredSubject[]> {
  const qs = new URLSearchParams({ section_id: params.section_id })
  return apiFetch<RequiredSubject[]>(`/api/solver/section-required-subjects?${qs.toString()}`)
}

export type AssignedTeacher = {
  teacher_id: string
  teacher_code: string
  teacher_name: string
  weekly_off_day?: number | null
}

export async function getAssignedTeacher(params: {
  section_id: string
  subject_id: string
}): Promise<AssignedTeacher> {
  const qs = new URLSearchParams({
    section_id: params.section_id,
    subject_id: params.subject_id,
  })
  return apiFetch<AssignedTeacher>(`/api/solver/assigned-teacher?${qs.toString()}`)
}
