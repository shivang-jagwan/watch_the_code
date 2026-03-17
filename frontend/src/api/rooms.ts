import { apiFetch } from './client'

export type Room = {
  id: string
  code: string
  name: string
  room_type: 'CLASSROOM' | 'LT' | 'LAB' | string
  capacity: number
  is_active: boolean
  is_special: boolean
  special_note?: string | null
  exclusive_subject_id?: string | null
  created_at: string
}

export type RoomCreate = {
  code: string
  name: string
  room_type: 'CLASSROOM' | 'LT' | 'LAB' | string
  capacity: number
  is_active: boolean
  is_special?: boolean
  special_note?: string | null
}

export type RoomExclusiveSubjectResponse = {
  room_id: string
  subject_id: string | null
}

export type RoomExclusiveSubjectOption = {
  id: string
  code: string
  name: string
}

export type RoomPut = RoomCreate

export async function listRooms(): Promise<Room[]> {
  return apiFetch<Room[]>('/api/rooms/')
}

export async function createRoom(payload: RoomCreate): Promise<Room> {
  return apiFetch<Room>('/api/rooms/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteRoom(id: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>(`/api/rooms/${id}`, { method: 'DELETE' })
}

export async function putRoom(id: string, payload: RoomPut): Promise<Room> {
  return apiFetch<Room>(`/api/rooms/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function putRoomWithForce(id: string, payload: RoomPut, force: boolean): Promise<Room> {
  const qs = force ? '?force=true' : ''
  return apiFetch<Room>(`/api/rooms/${id}${qs}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function getRoomExclusiveSubject(id: string): Promise<RoomExclusiveSubjectResponse> {
  return apiFetch<RoomExclusiveSubjectResponse>(`/api/rooms/${id}/exclusive-subject`)
}

export async function putRoomExclusiveSubject(
  id: string,
  subjectId: string | null,
): Promise<RoomExclusiveSubjectResponse> {
  return apiFetch<RoomExclusiveSubjectResponse>(`/api/rooms/${id}/exclusive-subject`, {
    method: 'PUT',
    body: JSON.stringify({ subject_id: subjectId }),
  })
}

export async function listRoomExclusiveSubjectOptions(): Promise<RoomExclusiveSubjectOption[]> {
  return apiFetch<RoomExclusiveSubjectOption[]>('/api/rooms/exclusive-subject-options')
}
