import { apiFetch } from './client'

export type ManualEditorEntry = {
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
  combined_class_id: string | null
  elective_block_id: string | null
  elective_block_name: string | null
  created_at: string
}

export type ManualEditorTeacher = {
  id: string
  code: string
  full_name: string
  weekly_off_day: number | null
}

export type ManualEditorRoom = {
  id: string
  code: string
  name: string
  room_type: string
}

export type ManualEditorSlot = {
  id: string
  day_of_week: number
  slot_index: number
  start_time: string
  end_time: string
}

export type ManualBoardData = {
  run_id: string
  run_status: string
  entries: ManualEditorEntry[]
  slots: ManualEditorSlot[]
  teachers: ManualEditorTeacher[]
  rooms: ManualEditorRoom[]
}

export async function getManualEditorBoard(params: {
  program_code: string
  run_id?: string
}): Promise<ManualBoardData> {
  const qs = new URLSearchParams({ program_code: params.program_code })
  if (params.run_id) qs.set('run_id', params.run_id)
  return apiFetch<ManualBoardData>(`/api/manual-editor/board?${qs}`)
}

export type ManualSaveEntryIn = {
  section_id: string
  subject_id: string
  teacher_id: string
  room_id: string
  slot_id: string
  combined_class_id?: string | null
  elective_block_id?: string | null
}

export async function saveManualEdits(payload: {
  source_run_id: string
  program_code: string
  entries: ManualSaveEntryIn[]
}): Promise<{ run_id: string; entries_written: number }> {
  return apiFetch<{ run_id: string; entries_written: number }>('/api/manual-editor/save', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
