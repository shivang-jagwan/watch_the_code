import { apiFetch } from './client'

export type TrackSubject = {
  id: string
  program_id: string
  track: 'CORE' | 'CYBER' | 'AI_DS' | 'AI_ML' | string
  subject_id: string
  is_elective: boolean
  sessions_override?: number | null
  created_at: string
}

export type TrackSubjectCreate = {
  program_code: string
  academic_year_number: number
  track: 'CORE' | 'CYBER' | 'AI_DS' | 'AI_ML' | string
  subject_code: string
  is_elective: boolean
  sessions_override?: number | null
}

export async function listTrackSubjects(params: {
  program_code: string
  academic_year_number: number
}): Promise<TrackSubject[]> {
  const qs = new URLSearchParams({
    program_code: params.program_code,
    academic_year_number: String(params.academic_year_number),
  })
  return apiFetch<TrackSubject[]>(`/api/curriculum/track-subjects?${qs.toString()}`)
}

export async function createTrackSubject(payload: TrackSubjectCreate): Promise<TrackSubject> {
  return apiFetch<TrackSubject>('/api/curriculum/track-subjects', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteTrackSubject(id: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>(`/api/curriculum/track-subjects/${id}`, { method: 'DELETE' })
}

// ── Curriculum Subjects ──────────────────────────────────────────────────────

export type CurriculumSubject = {
  id: string
  program_id: string
  academic_year_id: string
  track: string
  subject_id: string
  sessions_per_week: number
  max_per_day: number
  lab_block_size_slots: number
  is_elective: boolean
  created_at: string
}

export type CurriculumSubjectCreate = {
  program_code: string
  academic_year_number: number
  track?: string
  subject_code: string
  sessions_per_week?: number
  max_per_day?: number
  lab_block_size_slots?: number
  is_elective?: boolean
}

export type CurriculumSubjectUpdate = {
  track?: string
  sessions_per_week?: number
  max_per_day?: number
  lab_block_size_slots?: number
  is_elective?: boolean
}

export async function listCurriculumSubjects(params: {
  program_code: string
  academic_year_number: number
}): Promise<CurriculumSubject[]> {
  const qs = new URLSearchParams({
    program_code: params.program_code,
    academic_year_number: String(params.academic_year_number),
  })
  return apiFetch<CurriculumSubject[]>(`/api/curriculum/curriculum-subjects?${qs.toString()}`)
}

export async function createCurriculumSubject(
  payload: CurriculumSubjectCreate,
): Promise<CurriculumSubject> {
  return apiFetch<CurriculumSubject>('/api/curriculum/curriculum-subjects', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateCurriculumSubject(
  id: string,
  payload: CurriculumSubjectUpdate,
): Promise<CurriculumSubject> {
  return apiFetch<CurriculumSubject>(`/api/curriculum/curriculum-subjects/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteCurriculumSubject(id: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>(`/api/curriculum/curriculum-subjects/${id}`, { method: 'DELETE' })
}
