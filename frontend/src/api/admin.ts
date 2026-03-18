import { apiFetch } from './client'

export type AdminActionResult = {
  ok: boolean
  created: number
  updated: number
  deleted: number
  message?: string | null
}

export type AcademicYearOut = {
  id: string
  year_number: number
  is_active: boolean
  created_at: string
}

export async function listAcademicYears(): Promise<AcademicYearOut[]> {
  return apiFetch<AcademicYearOut[]>('/api/admin/academic-years')
}

export async function ensureAcademicYears(payload?: {
  year_numbers?: number[]
  activate?: boolean
}): Promise<AdminActionResult> {
  return apiFetch<AdminActionResult>('/api/admin/academic-years/ensure', {
    method: 'POST',
    body: JSON.stringify({
      year_numbers: payload?.year_numbers ?? [1, 2, 3, 4],
      activate: payload?.activate ?? true,
    }),
  })
}

export type MapProgramDataToYearResponse = {
  ok: boolean
  from_academic_year_number: number
  to_academic_year_number: number
  deleted: Record<string, number>
  updated: Record<string, number>
  message?: string | null
}

export async function mapProgramDataToYear(payload: {
  program_code: string
  from_academic_year_number: number
  to_academic_year_number: number
  replace_target?: boolean
  dry_run?: boolean
}): Promise<MapProgramDataToYearResponse> {
  return apiFetch<MapProgramDataToYearResponse>('/api/admin/programs/map-data-to-year', {
    method: 'POST',
    body: JSON.stringify({
      ...payload,
      replace_target: payload.replace_target ?? false,
      dry_run: payload.dry_run ?? false,
    }),
  })
}

export type GenerateTimeSlotsRequest = {
  days: number[]
  start_time: string // HH:MM
  end_time: string // HH:MM
  slot_minutes: number
  replace_existing: boolean
}

export async function generateTimeSlots(
  payload: GenerateTimeSlotsRequest,
): Promise<AdminActionResult> {
  return apiFetch<AdminActionResult>('/api/admin/time-slots/generate', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function toggleLunchBreak(slotId: string): Promise<AdminActionResult> {
  return apiFetch<AdminActionResult>(`/api/admin/time-slots/${slotId}/lunch-break`, {
    method: 'PATCH',
  })
}

export type ClearTimetablesRequest = {
  confirm: string
  program_code?: string | null
  academic_year_number?: number | null
}

export async function clearTimetables(payload: ClearTimetablesRequest): Promise<AdminActionResult> {
  return apiFetch<AdminActionResult>('/api/admin/timetables/clear', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export type DeleteTimetableRunRequest = {
  confirm: string
  run_id: string
}

export async function deleteTimetableRun(
  payload: DeleteTimetableRunRequest,
): Promise<AdminActionResult> {
  return apiFetch<AdminActionResult>('/api/admin/timetables/runs/delete', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export type TeacherSubjectSectionRef = {
  section_id: string
  section_code: string
  section_name: string
}

export type TeacherSubjectSectionAssignmentRow = {
  teacher_id: string
  teacher_code?: string | null
  teacher_name?: string | null

  subject_id: string
  subject_code: string
  subject_name: string

  sections: TeacherSubjectSectionRef[]
}

export async function listTeacherSubjectSections(params?: {
  teacher_id?: string
  subject_id?: string
  section_id?: string
}): Promise<TeacherSubjectSectionAssignmentRow[]> {
  const qs = new URLSearchParams()
  if (params?.teacher_id) qs.set('teacher_id', params.teacher_id)
  if (params?.subject_id) qs.set('subject_id', params.subject_id)
  if (params?.section_id) qs.set('section_id', params.section_id)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return apiFetch<TeacherSubjectSectionAssignmentRow[]>(`/api/admin/teacher-subject-sections${suffix}`)
}

export async function setTeacherSubjectSections(payload: {
  teacher_id: string
  subject_id: string
  section_ids: string[]
}): Promise<AdminActionResult> {
  return apiFetch<AdminActionResult>('/api/admin/teacher-subject-sections', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export type CombinedSubjectGroupSectionOut = {
  section_id: string
  section_code: string
  section_name: string
}

export type CombinedSubjectGroupOut = {
  id: string
  academic_year_number: number
  subject_id: string
  subject_code: string
  subject_name: string
  teacher_id?: string | null
  teacher_code?: string | null
  teacher_name?: string | null
  label?: string | null
  sections: CombinedSubjectGroupSectionOut[]
  created_at: string
}

export async function listCombinedSubjectGroups(params: {
  program_code: string
  academic_year_number: number
  subject_code?: string
}): Promise<CombinedSubjectGroupOut[]> {
  const qs = new URLSearchParams({
    program_code: params.program_code,
    academic_year_number: String(params.academic_year_number),
  })
  if (params.subject_code) qs.set('subject_code', params.subject_code)
  return apiFetch<CombinedSubjectGroupOut[]>(`/api/admin/combined-subject-groups?${qs.toString()}`)
}

export async function createCombinedSubjectGroup(payload: {
  program_code: string
  academic_year_number: number
  subject_code: string
  teacher_code: string
  label?: string | null
  section_codes: string[]
}): Promise<CombinedSubjectGroupOut> {
  return apiFetch<CombinedSubjectGroupOut>('/api/admin/combined-subject-groups', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateCombinedSubjectGroup(
  group_id: string,
  payload: {
    teacher_code: string
    label?: string | null
    section_codes: string[]
  },
): Promise<CombinedSubjectGroupOut> {
  return apiFetch<CombinedSubjectGroupOut>(`/api/admin/combined-subject-groups/${group_id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function deleteCombinedSubjectGroup(group_id: string): Promise<{ ok: boolean; deleted: number }>
{
  return apiFetch<{ ok: boolean; deleted: number }>(`/api/admin/combined-subject-groups/${group_id}`, {
    method: 'DELETE',
  })
}

export type ElectiveBlockSubjectOut = {
  id: string
  subject_id: string
  subject_code: string
  subject_name: string
  subject_type: string

  teacher_id: string
  teacher_code?: string | null
  teacher_name?: string | null
}

export type ElectiveBlockSectionOut = {
  section_id: string
  section_code: string
  section_name: string
}

export type ElectiveBlockOut = {
  id: string
  academic_year_number: number
  name: string
  code?: string | null
  is_active: boolean
  subjects: ElectiveBlockSubjectOut[]
  sections: ElectiveBlockSectionOut[]
  created_at: string
}

export async function listElectiveBlocks(params: {
  program_code: string
  academic_year_number: number
}): Promise<ElectiveBlockOut[]> {
  const qs = new URLSearchParams({
    program_code: params.program_code,
    academic_year_number: String(params.academic_year_number),
  })
  return apiFetch<ElectiveBlockOut[]>(`/api/admin/elective-blocks?${qs.toString()}`)
}

export async function createElectiveBlock(payload: {
  program_code: string
  academic_year_number: number
  name: string
  code?: string | null
  is_active?: boolean
}): Promise<ElectiveBlockOut> {
  return apiFetch<ElectiveBlockOut>('/api/admin/elective-blocks', {
    method: 'POST',
    body: JSON.stringify({
      ...payload,
      is_active: payload.is_active ?? true,
    }),
  })
}

export async function getElectiveBlock(params: {
  block_id: string
  academic_year_number: number
}): Promise<ElectiveBlockOut> {
  const qs = new URLSearchParams({ academic_year_number: String(params.academic_year_number) })
  return apiFetch<ElectiveBlockOut>(`/api/admin/elective-blocks/${params.block_id}?${qs.toString()}`)
}

export async function updateElectiveBlock(params: {
  block_id: string
  academic_year_number: number
  name?: string
  code?: string | null
  is_active?: boolean
}): Promise<ElectiveBlockOut> {
  const qs = new URLSearchParams({ academic_year_number: String(params.academic_year_number) })
  const body: any = {}
  if (params.name !== undefined) body.name = params.name
  if (params.code !== undefined) body.code = params.code
  if (params.is_active !== undefined) body.is_active = params.is_active

  return apiFetch<ElectiveBlockOut>(`/api/admin/elective-blocks/${params.block_id}?${qs.toString()}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export async function deleteElectiveBlock(block_id: string): Promise<{ ok: boolean; deleted: number }> {
  return apiFetch<{ ok: boolean; deleted: number }>(`/api/admin/elective-blocks/${block_id}`, {
    method: 'DELETE',
  })
}

export async function upsertElectiveBlockSubject(payload: {
  block_id: string
  subject_id: string
  teacher_id: string
}): Promise<AdminActionResult> {
  return apiFetch<AdminActionResult>(`/api/admin/elective-blocks/${payload.block_id}/subjects`, {
    method: 'POST',
    body: JSON.stringify({ subject_id: payload.subject_id, teacher_id: payload.teacher_id }),
  })
}

export async function deleteElectiveBlockSubject(payload: {
  block_id: string
  assignment_id: string
}): Promise<AdminActionResult> {
  return apiFetch<AdminActionResult>(
    `/api/admin/elective-blocks/${payload.block_id}/subjects/${payload.assignment_id}`,
    {
    method: 'DELETE',
    },
  )
}

export async function setElectiveBlockSections(payload: {
  block_id: string
  section_ids: string[]
}): Promise<AdminActionResult> {
  return apiFetch<AdminActionResult>(`/api/admin/elective-blocks/${payload.block_id}/sections`, {
    method: 'PUT',
    body: JSON.stringify({ section_ids: payload.section_ids }),
  })
}
