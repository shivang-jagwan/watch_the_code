import React from 'react'
import type { Subject, SubjectPut } from '../api/subjects'
import {
  getSubjectAllowedRooms,
  addSubjectAllowedRoom,
  removeSubjectAllowedRoom,
} from '../api/subjects'
import { listRooms } from '../api/rooms'
import type { Room } from '../api/rooms'
import { useModalScrollLock } from '../hooks/useModalScrollLock'
import { PremiumSelect } from './PremiumSelect'

const SUBJECT_TYPES = [
  { label: 'Theory', value: 'THEORY' },
  { label: 'Lab', value: 'LAB' },
]

export type SubjectEditModalProps = {
  open: boolean
  subject: Subject | null
  loading?: boolean
  onClose: () => void
  onSave: (payload: SubjectPut) => Promise<void> | void
}

type FormState = {
  name: string
  subject_type: 'THEORY' | 'LAB' | string
  sessions_per_week: number
  max_per_day: number
  lab_block_size_slots: number
  is_active: boolean
}

function subjectToForm(s: Subject): FormState {
  return {
    name: s.name ?? '',
    subject_type: s.subject_type ?? 'THEORY',
    sessions_per_week: Number(s.sessions_per_week ?? 1),
    max_per_day: Number(s.max_per_day ?? 1),
    lab_block_size_slots: Number(s.lab_block_size_slots ?? 1),
    is_active: Boolean(s.is_active),
  }
}

function normalizeForm(f: FormState): FormState {
  const st = String(f.subject_type).toUpperCase()
  if (st === 'THEORY') {
    return { ...f, subject_type: 'THEORY', lab_block_size_slots: 1 }
  }
  if (st === 'LAB') {
    return { ...f, subject_type: 'LAB' }
  }
  return f
}

function validateForm(f: FormState): string[] {
  const errors: string[] = []
  const st = String(f.subject_type).toUpperCase()

  if (!f.name.trim()) errors.push('Name is required')

  if (Number.isNaN(f.sessions_per_week) || f.sessions_per_week < 1) errors.push('Sessions/week must be >= 1')
  if (f.sessions_per_week > 6) errors.push('Sessions/week cannot exceed 6')

  if (Number.isNaN(f.max_per_day) || f.max_per_day < 1) errors.push('Max/day must be >= 1')
  if (f.max_per_day > f.sessions_per_week) errors.push('Max/day must be <= Sessions/week')

  if (st === 'THEORY') {
    if (f.lab_block_size_slots !== 1) errors.push('For THEORY, Lab block size must be 1')
  } else if (st === 'LAB') {
    if (Number.isNaN(f.lab_block_size_slots) || f.lab_block_size_slots < 2)
      errors.push('For LAB, Lab block size must be >= 2')
  } else {
    errors.push('Type must be THEORY or LAB')
  }

  if (f.sessions_per_week * f.lab_block_size_slots > 12) {
    errors.push('Sessions/week × Lab block size cannot exceed 12')
  }

  return errors
}

export function SubjectEditModal({ open, subject, loading, onClose, onSave }: SubjectEditModalProps) {
  useModalScrollLock(open)

  const [form, setForm] = React.useState<FormState | null>(null)
  const [errors, setErrors] = React.useState<string[]>([])

  // Allowed rooms
  const [allRooms, setAllRooms] = React.useState<Room[]>([])
  const [allowedRoomIds, setAllowedRoomIds] = React.useState<Set<string>>(new Set())
  const [roomsSaving, setRoomsSaving] = React.useState(false)

  React.useEffect(() => {
    if (!open || !subject) {
      setForm(null)
      setErrors([])
      setAllowedRoomIds(new Set())
      return
    }
    setForm(subjectToForm(subject))
    setErrors([])
    // Load rooms + allowed rooms in parallel
    Promise.all([
      listRooms().catch(() => [] as Room[]),
      getSubjectAllowedRooms(subject.id).catch(() => ({ subject_id: subject.id, room_ids: [] })),
    ]).then(([rooms, sar]) => {
      setAllRooms(rooms)
      setAllowedRoomIds(new Set(sar.room_ids))
    })
  }, [open, subject])

  React.useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    if (open) window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open || !subject || !form) return null

  const idPrefix = `edit_subject_${subject.id}`
  const normalized = normalizeForm(form)
  const subjectType = String(normalized.subject_type).toUpperCase()

  async function handleToggleRoom(roomId: string) {
    if (!subject || roomsSaving) return
    setRoomsSaving(true)
    try {
      if (allowedRoomIds.has(roomId)) {
        await removeSubjectAllowedRoom(subject.id, roomId)
        setAllowedRoomIds((prev) => { const n = new Set(prev); n.delete(roomId); return n })
      } else {
        await addSubjectAllowedRoom(subject.id, roomId)
        setAllowedRoomIds((prev) => new Set(prev).add(roomId))
      }
    } catch {
      // ignore — room state will be out-of-sync but non-critical
    } finally {
      setRoomsSaving(false)
    }
  }

  async function handleSave() {
    if (!form) return
    const current = normalizeForm(form)
    const nextErrors = validateForm(current)
    setErrors(nextErrors)
    if (nextErrors.length) return

    const payload: SubjectPut = {
      name: current.name.trim(),
      subject_type: String(current.subject_type).toUpperCase(),
      sessions_per_week: Number(current.sessions_per_week),
      max_per_day: Number(current.max_per_day),
      lab_block_size_slots: Number(current.lab_block_size_slots),
      is_active: Boolean(current.is_active),
    }

    await onSave(payload)
  }

  return (
    <div
      className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center z-50 animate-fadeIn p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-[640px] max-h-[90vh] overflow-y-auto bg-white/80 backdrop-blur-lg rounded-2xl shadow-2xl p-6 border border-white/40 animate-scaleIn"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-lg font-semibold text-slate-900">Edit Subject</div>
            <div className="mt-1 text-xs text-slate-500">
              Updates affect every new timetable run (data-driven).
            </div>
          </div>
          <button
            className="btn-secondary text-xs font-medium text-slate-800 disabled:opacity-50"
            onClick={onClose}
            disabled={Boolean(loading)}
            type="button"
          >
            Close
          </button>
        </div>

          <div className="mt-4 grid gap-3">
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor={`${idPrefix}_code`} className="text-xs font-medium text-slate-600">
                  Code
                </label>
                <input
                  id={`${idPrefix}_code`}
                  className="input-premium mt-1 w-full text-sm bg-slate-50 text-slate-700"
                  value={subject.code}
                  disabled
                />
              </div>
              <div>
                <label htmlFor={`${idPrefix}_name`} className="text-xs font-medium text-slate-600">
                  Name (required)
                </label>
                <input
                  id={`${idPrefix}_name`}
                  className="input-premium mt-1 w-full text-sm"
                  value={normalized.name}
                  onChange={(e) => setForm((f) => (f ? { ...f, name: e.target.value } : f))}
                />
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor={`${idPrefix}_type`} className="text-xs font-medium text-slate-600">
                  Type
                </label>
                <PremiumSelect
                  id={`${idPrefix}_type`}
                  ariaLabel="Subject type"
                  className="mt-1 text-sm"
                  value={subjectType}
                  onValueChange={(nextType) => {
                    setForm((f) => {
                      if (!f) return f
                      if (nextType === 'THEORY') return { ...f, subject_type: 'THEORY', lab_block_size_slots: 1 }
                      return { ...f, subject_type: 'LAB' }
                    })
                  }}
                  options={SUBJECT_TYPES.map((t) => ({ value: t.value, label: t.label }))}
                />
              </div>

              <label className="checkbox-row rounded-lg border border-white/40 bg-white/70 md:mt-6">
                <input
                  type="checkbox"
                  checked={normalized.is_active}
                  onChange={(e) => setForm((f) => (f ? { ...f, is_active: e.target.checked } : f))}
                />
                <span className="text-slate-700 font-medium">Active</span>
              </label>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <label htmlFor={`${idPrefix}_spw`} className="text-xs font-medium text-slate-600">
                  Sessions/week
                </label>
                <input
                  id={`${idPrefix}_spw`}
                  type="number"
                  min={1}
                  max={6}
                  className="input-premium mt-1 w-full text-sm"
                  value={normalized.sessions_per_week}
                  onChange={(e) =>
                    setForm((f) => (f ? { ...f, sessions_per_week: Number(e.target.value) } : f))
                  }
                />
              </div>
              <div>
                <label htmlFor={`${idPrefix}_mpd`} className="text-xs font-medium text-slate-600">
                  Max/day
                </label>
                <input
                  id={`${idPrefix}_mpd`}
                  type="number"
                  min={1}
                  className="input-premium mt-1 w-full text-sm"
                  value={normalized.max_per_day}
                  onChange={(e) => setForm((f) => (f ? { ...f, max_per_day: Number(e.target.value) } : f))}
                />
              </div>
              <div>
                {subjectType === 'LAB' ? (
                  <>
                    <label htmlFor={`${idPrefix}_lbs`} className="text-xs font-medium text-slate-600">
                      Lab block size
                    </label>
                    <input
                      id={`${idPrefix}_lbs`}
                      type="number"
                      min={2}
                      className="input-premium mt-1 w-full text-sm"
                      value={normalized.lab_block_size_slots}
                      onChange={(e) =>
                        setForm((f) =>
                          f ? { ...f, lab_block_size_slots: Number(e.target.value) } : f,
                        )
                      }
                    />
                  </>
                ) : (
                  <div className="mt-6 text-xs text-slate-500">THEORY uses block size 1.</div>
                )}
              </div>
            </div>

            {errors.length > 0 && (
              <div className="rounded-2xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <div className="text-xs font-semibold">Please fix:</div>
                <ul className="mt-1 list-disc pl-5 text-xs">
                  {errors.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* ── Allowed Rooms ─────────────────────────────────────────── */}
            <div className="mt-2 rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <div className="text-sm font-semibold text-slate-900">Allowed Rooms</div>
              <div className="mt-1 text-xs text-slate-500">
                Optionally restrict this subject to specific rooms. Leave all unchecked to allow
                any compatible room.
              </div>
              {allRooms.filter((r) => r.is_active).length === 0 ? (
                <p className="mt-3 text-xs italic text-slate-400">No rooms configured.</p>
              ) : (
                <div className="mt-3 grid gap-1 sm:grid-cols-2">
                  {allRooms
                    .filter((r) => r.is_active)
                    .map((r) => (
                      <label
                        key={r.id}
                        className={
                          'flex cursor-pointer select-none items-center gap-2 rounded-xl border px-3 py-2 text-xs transition-colors ' +
                          (allowedRoomIds.has(r.id)
                            ? 'border-emerald-300 bg-emerald-50 text-emerald-900'
                            : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300')
                        }
                      >
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 accent-emerald-600"
                          checked={allowedRoomIds.has(r.id)}
                          disabled={roomsSaving}
                          onChange={() => handleToggleRoom(r.id)}
                        />
                        <span className="font-medium">{r.code}</span>
                        <span className="text-slate-500">{r.room_type}</span>
                        {r.is_special && (
                          <span className="ml-auto rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
                            Special
                          </span>
                        )}
                      </label>
                    ))}
                </div>
              )}
              {allowedRoomIds.size > 0 && (
                <p className="mt-2 text-xs text-emerald-700 font-medium">
                  {allowedRoomIds.size} room{allowedRoomIds.size > 1 ? 's' : ''} selected — solver
                  will only assign this subject to these rooms.
                </p>
              )}
            </div>

            <div className="mt-1 flex items-center justify-end gap-2">
              <button
                className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
                onClick={onClose}
                disabled={Boolean(loading)}
                type="button"
              >
                Cancel
              </button>
              <button
                className="btn-primary text-sm font-semibold disabled:opacity-50"
                onClick={handleSave}
                disabled={Boolean(loading)}
                type="button"
              >
                {loading ? 'Saving…' : 'Save Changes'}
              </button>
            </div>
          </div>
      </div>
    </div>
  )
}
