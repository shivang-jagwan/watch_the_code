import React from 'react'
import { Toast } from '../components/Toast'
import {
  createRoom,
  deleteRoom,
  listRooms,
  listRoomExclusiveSubjectOptions,
  putRoom,
  putRoomExclusiveSubject,
  putRoomWithForce,
  Room,
  RoomExclusiveSubjectOption,
} from '../api/rooms'
import { RoomEditModal } from '../components/RoomEditModal'
import { PremiumSelect } from '../components/PremiumSelect'

const ROOM_TYPES = [
  { label: 'Classroom', value: 'CLASSROOM' },
  { label: 'Lecture Theatre', value: 'LT' },
  { label: 'Lab', value: 'LAB' },
]

export function Rooms() {
  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [items, setItems] = React.useState<Room[]>([])
  const [subjectOptions, setSubjectOptions] = React.useState<RoomExclusiveSubjectOption[]>([])
  const [query, setQuery] = React.useState('')

  const [editOpen, setEditOpen] = React.useState(false)
  const [editRoom, setEditRoom] = React.useState<Room | null>(null)

  const [form, setForm] = React.useState({
    code: '',
    name: '',
    room_type: 'CLASSROOM',
    capacity: 0,
    is_active: true,
    is_special: false,
    special_note: '',
    is_exclusive_room: false,
    exclusive_subject_id: '',
  })

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  async function refresh() {
    setLoading(true)
    try {
      const [data, options] = await Promise.all([
        listRooms(),
        listRoomExclusiveSubjectOptions().catch(() => [] as RoomExclusiveSubjectOption[]),
      ])
      setItems(data)
      setSubjectOptions(options)
    } catch (e: any) {
      showToast(`Load failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  React.useEffect(() => {
    refresh()
  }, [])

  async function onCreate() {
    setLoading(true)
    try {
      const created = await createRoom({
        code: form.code.trim(),
        name: form.name.trim(),
        room_type: form.room_type,
        capacity: Number(form.capacity),
        is_active: Boolean(form.is_active),
        is_special: Boolean(form.is_special),
        special_note: form.special_note.trim() ? form.special_note.trim() : null,
      })
      await putRoomExclusiveSubject(
        created.id,
        form.is_exclusive_room ? form.exclusive_subject_id || null : null,
      )
      showToast('Room saved')
      setForm((f) => ({ ...f, code: '', name: '', special_note: '', is_exclusive_room: false, exclusive_subject_id: '' }))
      await refresh()
    } catch (e: any) {
      showToast(`Save failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function onDelete(id: string) {
    if (!confirm('Delete this room?')) return
    setLoading(true)
    try {
      await deleteRoom(id)
      showToast('Room deleted')
      await refresh()
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  function openEdit(room: Room) {
    setEditRoom(room)
    setEditOpen(true)
  }

  function closeEdit() {
    setEditOpen(false)
    setEditRoom(null)
  }

  async function onSaveEdit(payload: any, exclusiveSubjectId: string | null) {
    if (!editRoom) return
    setLoading(true)
    try {
      const nextPayload = {
        code: String(payload.code ?? '').trim(),
        name: String(payload.name ?? '').trim(),
        room_type: payload.room_type,
        capacity: Number(payload.capacity),
        is_active: Boolean(payload.is_active),
        is_special: Boolean(payload.is_special),
        special_note: String(payload.special_note ?? '').trim() ? String(payload.special_note ?? '').trim() : null,
      }

      try {
        await putRoom(editRoom.id, nextPayload)
      } catch (e: any) {
        const msg = String(e?.message ?? e)
        if (msg.includes('ROOM_IN_USE_CONFIRM_REQUIRED')) {
          const ok = window.confirm(
            'This room is already used in existing timetables/fixed entries/locks. Marking it as special can break those references.\n\nContinue?',
          )
          if (!ok) throw e
          await putRoomWithForce(editRoom.id, nextPayload, true)
        } else {
          throw e
        }
      }
      await putRoomExclusiveSubject(editRoom.id, exclusiveSubjectId)
      showToast('Room updated')
      closeEdit()
      await refresh()
    } catch (e: any) {
      showToast(`Update failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter((r) => r.code.toLowerCase().includes(q) || r.name.toLowerCase().includes(q))
  }, [items, query])

  return (
    <div className="space-y-6">
      <Toast message={toast} />

      <RoomEditModal
        open={editOpen}
        room={editRoom}
        subjects={subjectOptions}
        loading={loading}
        onClose={closeEdit}
        onSave={onSaveEdit}
      />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-slate-900">Rooms</div>
          <div className="mt-1 text-sm text-slate-600">Manage classrooms, lecture theatres, and labs.</div>
        </div>
        <button
          className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
          onClick={refresh}
          disabled={loading}
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        <section className="rounded-3xl border bg-white p-5">
          <div className="text-sm font-semibold text-slate-900">Add Room</div>
          <div className="mt-5 grid gap-3">
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor="room_code" className="text-xs font-medium text-slate-600">
                  Code
                </label>
                <input
                  id="room_code"
                  className="input-premium mt-1 w-full text-sm"
                  value={form.code}
                  onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                  placeholder="LT-1"
                />
              </div>
              <div>
                <label htmlFor="room_type" className="text-xs font-medium text-slate-600">
                  Type
                </label>
                <PremiumSelect
                  id="room_type"
                  ariaLabel="Room type"
                  className="mt-1 text-sm"
                  value={form.room_type}
                  onValueChange={(v) => setForm((f) => ({ ...f, room_type: v }))}
                  options={ROOM_TYPES.map((t) => ({ value: t.value, label: t.label }))}
                />
              </div>
            </div>

            <div>
              <label htmlFor="room_name" className="text-xs font-medium text-slate-600">
                Name
              </label>
              <input
                id="room_name"
                className="input-premium mt-1 w-full text-sm"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Lecture Theatre 1"
              />
            </div>

            <div>
              <label htmlFor="room_cap" className="text-xs font-medium text-slate-600">
                Capacity
              </label>
              <input
                id="room_cap"
                type="number"
                min={0}
                className="input-premium mt-1 w-full text-sm"
                value={form.capacity}
                onChange={(e) => setForm((f) => ({ ...f, capacity: Number(e.target.value) }))}
              />
            </div>

            <label className="flex select-none items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                className="h-4 w-4 accent-emerald-600"
                checked={form.is_active}
                onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
              />
              Active
            </label>

            <label className="flex select-none items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                className="h-4 w-4 accent-amber-600"
                checked={form.is_special}
                onChange={(e) => setForm((f) => ({ ...f, is_special: e.target.checked }))}
              />
              Special room (🔒)
            </label>

            <label className="flex select-none items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                className="h-4 w-4 accent-emerald-600"
                checked={form.is_exclusive_room}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    is_exclusive_room: e.target.checked,
                    exclusive_subject_id: e.target.checked ? f.exclusive_subject_id : '',
                  }))
                }
              />
              Exclusive room
            </label>

            {form.is_exclusive_room ? (
              <div>
                <label htmlFor="room_exclusive_subject" className="text-xs font-medium text-slate-600">
                  Select Subject
                </label>
                <PremiumSelect
                  id="room_exclusive_subject"
                  ariaLabel="Select exclusive subject"
                  className="mt-1 text-sm"
                  value={form.exclusive_subject_id}
                  onValueChange={(v) => setForm((f) => ({ ...f, exclusive_subject_id: v }))}
                  options={subjectOptions.map((s) => ({ value: s.id, label: `${s.code} - ${s.name}` }))}
                  placeholder="Select subject"
                />
              </div>
            ) : null}

            {form.is_special ? (
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                Special rooms are never auto-assigned by the solver. Use Special Allotments to place them.
              </div>
            ) : null}

            <div>
              <label htmlFor="room_special_note" className="text-xs font-medium text-slate-600">
                Special note (optional)
              </label>
              <input
                id="room_special_note"
                className="input-premium mt-1 w-full text-sm"
                value={form.special_note}
                onChange={(e) => setForm((f) => ({ ...f, special_note: e.target.value }))}
                placeholder="e.g., Exam hall / Seminar room"
              />
            </div>

            <button
              className="btn-primary text-sm font-semibold disabled:opacity-50"
              onClick={onCreate}
              disabled={
                loading ||
                !form.code.trim() ||
                !form.name.trim() ||
                (form.is_exclusive_room && !form.exclusive_subject_id)
              }
            >
              {loading ? 'Saving…' : 'Save Room'}
            </button>
          </div>
        </section>

        <section className="rounded-3xl border bg-white p-5">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-900">Directory</div>
              <div className="mt-1 text-xs text-slate-500">{items.length} total</div>
            </div>
            <div className="w-full sm:w-72">
              <label htmlFor="room_search" className="text-xs font-medium text-slate-600">
                Search
              </label>
              <input
                id="room_search"
                className="input-premium mt-1 w-full text-sm"
                placeholder="Code or name…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          </div>

          <div className="mt-4 overflow-auto rounded-2xl border">
            <table className="w-full border-collapse bg-white text-sm">
              <thead className="bg-slate-50 text-xs text-slate-600">
                <tr>
                  <th className="px-3 py-2 text-left font-semibold">Code</th>
                  <th className="px-3 py-2 text-left font-semibold">Name</th>
                  <th className="px-3 py-2 text-left font-semibold">Type</th>
                  <th className="px-3 py-2 text-left font-semibold">Special</th>
                  <th className="px-3 py-2 text-left font-semibold">Capacity</th>
                  <th className="px-3 py-2 text-left font-semibold">Active</th>
                  <th className="px-3 py-2 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td className="px-3 py-4 text-slate-600" colSpan={7}>
                      No matching rooms.
                    </td>
                  </tr>
                ) : (
                  filtered.map((r) => (
                    <tr key={r.id} className="border-t">
                      <td className="px-3 py-2 font-medium text-slate-900">
                        {r.is_special ? '🔒 ' : ''}
                        {r.code}
                      </td>
                      <td className="px-3 py-2 text-slate-800">{r.name}</td>
                      <td className="px-3 py-2 text-slate-700">{r.room_type}</td>
                      <td className="px-3 py-2">
                        {r.is_special ? (
                          <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-700">
                            Special
                          </span>
                        ) : (
                          <span className="text-xs text-slate-400">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-slate-700">{r.capacity}</td>
                      <td className="px-3 py-2">
                        <span
                          className={
                            'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ' +
                            (r.is_active
                              ? 'bg-emerald-50 text-emerald-700'
                              : 'bg-slate-100 text-slate-600')
                          }
                        >
                          {r.is_active ? 'Active' : 'Inactive'}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          className="btn-secondary mr-2 text-xs font-medium text-slate-800 disabled:opacity-50"
                          onClick={() => openEdit(r)}
                          disabled={loading}
                        >
                          Edit
                        </button>
                        <button
                          className="btn-danger text-xs font-semibold disabled:opacity-50"
                          onClick={() => onDelete(r.id)}
                          disabled={loading}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  )
}
