import { apiFetch } from './client'

export type Section = {
  id: string
  program_id: string
  code: string
  name: string
  strength: number
  track: 'CORE' | 'CYBER' | 'AI_DS' | 'AI_ML' | string
  is_active: boolean
  created_at: string
}

export type SectionCreate = {
  program_code: string
  academic_year_number: number
  code: string
  name: string
  strength: number
  track: 'CORE' | 'CYBER' | 'AI_DS' | 'AI_ML' | string
  is_active: boolean
}

export type SectionStrengthPut = {
  strength: number
}

export type SectionTimeWindow = {
  id: string
  section_id: string
  day_of_week: number
  start_slot_index: number
  end_slot_index: number
  created_at: string
}

export async function getSectionTimeWindows(sectionId: string): Promise<{
  section_id: string
  windows: SectionTimeWindow[]
}> {
  return apiFetch<{ section_id: string; windows: SectionTimeWindow[] }>(`/api/sections/${sectionId}/time-window`)
}

export async function putSectionTimeWindows(
  sectionId: string,
  payload: { windows: Array<{ day_of_week: number; start_slot_index: number; end_slot_index: number }> },
): Promise<{ section_id: string; windows: SectionTimeWindow[] }> {
  return apiFetch<{ section_id: string; windows: SectionTimeWindow[] }>(`/api/sections/${sectionId}/time-window`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function listSections(params: {
  program_code: string
  academic_year_number: number
}): Promise<Section[]> {
  const qs = new URLSearchParams({
    program_code: params.program_code,
    academic_year_number: String(params.academic_year_number),
  })
  return apiFetch<Section[]>(`/api/sections/?${qs.toString()}`)
}

export async function createSection(payload: SectionCreate): Promise<Section> {
  return apiFetch<Section>('/api/sections/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteSection(id: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>(`/api/sections/${id}`, { method: 'DELETE' })
}

export async function putSectionStrength(id: string, payload: SectionStrengthPut): Promise<Section> {
  return apiFetch<Section>(`/api/sections/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}
