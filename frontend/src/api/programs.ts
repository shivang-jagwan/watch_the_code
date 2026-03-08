import { apiFetch } from './client'

export type Program = {
  id: string
  code: string
  name: string
  created_at?: string
}

export type ProgramCreate = {
  code: string
  name: string
}

export async function listPrograms(): Promise<Program[]> {
  return apiFetch<Program[]>('/api/programs/')
}

export async function createProgram(payload: ProgramCreate): Promise<Program> {
  return apiFetch<Program>('/api/programs/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteProgram(programId: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>(`/api/programs/${encodeURIComponent(programId)}`, {
    method: 'DELETE',
  })
}

export async function getLatestProgram(): Promise<{ program_code: string }> {
  return apiFetch<{ program_code: string }>('/api/programs/latest')
}
