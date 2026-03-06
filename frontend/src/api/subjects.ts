import { apiFetch } from './client'

export type Subject = {
  id: string
  program_id: string
  code: string
  name: string
  subject_type: 'THEORY' | 'LAB' | string
  sessions_per_week: number
  max_per_day: number
  lab_block_size_slots: number
  is_active: boolean
  created_at: string
}

export type SubjectCreate = {
  program_code: string
  academic_year_number: number
  code: string
  name: string
  subject_type: 'THEORY' | 'LAB' | string
  sessions_per_week: number
  max_per_day: number
  lab_block_size_slots: number
  is_active: boolean
}

export type SubjectPut = Pick<
  Subject,
  | 'name'
  | 'subject_type'
  | 'sessions_per_week'
  | 'max_per_day'
  | 'lab_block_size_slots'
  | 'is_active'
>

export async function listSubjects(params: {
  program_code: string
  academic_year_number: number
}): Promise<Subject[]> {
  const qs = new URLSearchParams({
    program_code: params.program_code,
    academic_year_number: String(params.academic_year_number),
  })
  return apiFetch<Subject[]>(`/api/subjects/?${qs.toString()}`)
}

export async function createSubject(payload: SubjectCreate): Promise<Subject> {
  return apiFetch<Subject>('/api/subjects/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteSubject(id: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>(`/api/subjects/${id}`, { method: 'DELETE' })
}

export async function updateSubject(id: string, payload: SubjectPut): Promise<Subject> {
  return apiFetch<Subject>(`/api/subjects/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}
