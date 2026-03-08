import React from 'react'
import { Link } from 'react-router-dom'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  useDraggable,
  useDroppable,
  type DragStartEvent,
  type DragEndEvent,
} from '@dnd-kit/core'
import { useLayoutContext } from '../components/Layout'
import {
  getManualEditorBoard,
  saveManualEdits,
  type ManualEditorEntry,
  type ManualEditorTeacher,
  type ManualEditorRoom,
  type ManualEditorSlot,
} from '../api/manualEditor'
import { listRuns, type RunSummary } from '../api/solver'

// ─── Types ────────────────────────────────────────────────────────────────────

type BoardEntry = ManualEditorEntry & {
  _inHold: boolean
  _dirty: boolean
}

type ConflictItem = {
  type: 'TEACHER' | 'ROOM'
  message: string
  entryIds: string[]
}

type CellData = {
  type: 'CELL'
  section_id: string
  day_of_week: number
  slot_index: number
  slot_id: string
}

type HoldData = {
  type: 'HOLD'
}

type DropData = CellData | HoldData

// ─── Helpers ─────────────────────────────────────────────────────────────────

const DAY_LABELS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

function dayLabel(d: number): string {
  return DAY_LABELS[d] ?? `Day ${d}`
}

function detectConflicts(entries: BoardEntry[]): ConflictItem[] {
  const grid = entries.filter((e) => !e._inHold)
  const conflicts: ConflictItem[] = []

  const bySlot = new Map<string, BoardEntry[]>()
  for (const e of grid) {
    const key = `${e.day_of_week}__${e.slot_index}`
    if (!bySlot.has(key)) bySlot.set(key, [])
    bySlot.get(key)!.push(e)
  }

  for (const slotEntries of bySlot.values()) {
    // Teacher double-booking
    const byTeacher = new Map<string, BoardEntry[]>()
    for (const e of slotEntries) {
      if (!byTeacher.has(e.teacher_id)) byTeacher.set(e.teacher_id, [])
      byTeacher.get(e.teacher_id)!.push(e)
    }
    for (const tes of byTeacher.values()) {
      if (tes.length > 1) {
        const sections = tes.map((e) => e.section_code).join(', ')
        conflicts.push({
          type: 'TEACHER',
          message: `${tes[0].teacher_name}: double-booked on ${dayLabel(tes[0].day_of_week)} P${tes[0].slot_index + 1} (${sections})`,
          entryIds: tes.map((e) => e.id),
        })
      }
    }

    // Room double-booking (skip for legitimate combined classes)
    const byRoom = new Map<string, BoardEntry[]>()
    for (const e of slotEntries) {
      if (!byRoom.has(e.room_id)) byRoom.set(e.room_id, [])
      byRoom.get(e.room_id)!.push(e)
    }
    for (const tes of byRoom.values()) {
      if (tes.length > 1) {
        const allSameCombined =
          tes.every((e) => e.combined_class_id != null) &&
          tes.every((e) => e.combined_class_id === tes[0].combined_class_id)
        if (!allSameCombined) {
          const sections = tes.map((e) => e.section_code).join(', ')
          conflicts.push({
            type: 'ROOM',
            message: `${tes[0].room_code}: double-booked on ${dayLabel(tes[0].day_of_week)} P${tes[0].slot_index + 1} (${sections})`,
            entryIds: tes.map((e) => e.id),
          })
        }
      }
    }
  }

  return conflicts
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function DraggableCard({
  entry,
  conflictIds,
  onEdit,
}: {
  entry: BoardEntry
  conflictIds: Set<string>
  onEdit: (e: BoardEntry) => void
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: entry.id,
    data: entry,
  })

  const hasConflict = conflictIds.has(entry.id)

  let cardCls =
    'mb-1 cursor-grab select-none rounded px-1.5 py-1 text-xs leading-tight last:mb-0 transition-opacity'
  if (isDragging) {
    cardCls += ' opacity-30'
  } else if (hasConflict) {
    cardCls += ' bg-rose-100 ring-1 ring-rose-400 text-rose-900'
  } else if (entry._dirty) {
    cardCls += ' bg-amber-50 ring-1 ring-amber-400 text-amber-900'
  } else {
    cardCls += ' bg-indigo-50 ring-1 ring-indigo-200 text-slate-800 hover:ring-indigo-400'
  }

  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      className={cardCls}
      onClick={(ev) => {
        ev.stopPropagation()
        onEdit(entry)
      }}
    >
      <div className="font-semibold truncate">{entry.subject_code}</div>
      <div className="opacity-70 truncate">{entry.teacher_code}</div>
      <div className="opacity-70 truncate">{entry.room_code}</div>
      {entry._inHold && (
        <div className="opacity-70 truncate text-indigo-600">{entry.section_code}</div>
      )}
    </div>
  )
}

function CardPreview({ entry }: { entry: BoardEntry }) {
  return (
    <div className="w-24 cursor-grabbing rounded px-2 py-1.5 text-xs shadow-lg bg-indigo-100 ring-2 ring-indigo-500 text-slate-800">
      <div className="font-semibold truncate">{entry.subject_code}</div>
      <div className="opacity-70 truncate">{entry.teacher_code}</div>
      <div className="opacity-70 truncate">{entry.room_code}</div>
    </div>
  )
}

function DroppableCell({
  id,
  data,
  children,
}: {
  id: string
  data: CellData
  children: React.ReactNode
}) {
  const { isOver, setNodeRef } = useDroppable({ id, data })
  return (
    <td
      ref={setNodeRef}
      className={[
        'border border-slate-200 p-1 align-top w-[84px] min-w-[84px] transition-colors',
        isOver ? 'bg-blue-50 ring-2 ring-inset ring-blue-400' : 'bg-white hover:bg-slate-50',
      ].join(' ')}
    >
      {children}
    </td>
  )
}

function DroppableHold({ children }: { children: React.ReactNode }) {
  const { isOver, setNodeRef } = useDroppable({ id: 'HOLD', data: { type: 'HOLD' } as HoldData })
  return (
    <div
      ref={setNodeRef}
      className={[
        'min-h-[100px] rounded-xl border-2 border-dashed p-2 transition-colors flex flex-wrap gap-1 content-start',
        isOver ? 'border-blue-400 bg-blue-50' : 'border-slate-300 bg-slate-50',
      ].join(' ')}
    >
      {children}
    </div>
  )
}

function EditModal({
  entry,
  teachers,
  rooms,
  onSave,
  onClose,
}: {
  entry: BoardEntry
  teachers: ManualEditorTeacher[]
  rooms: ManualEditorRoom[]
  onSave: (teacherId: string, roomId: string) => void
  onClose: () => void
}) {
  const [teacherId, setTeacherId] = React.useState(entry.teacher_id)
  const [roomId, setRoomId] = React.useState(entry.room_id)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm space-y-4 rounded-2xl bg-white p-6 shadow-xl"
        onClick={(ev) => ev.stopPropagation()}
      >
        <div className="text-base font-semibold text-slate-900">Edit Entry</div>

        <div className="space-y-0.5 text-sm text-slate-600">
          <div>
            <span className="font-medium">Section:</span> {entry.section_code}
          </div>
          <div>
            <span className="font-medium">Subject:</span> {entry.subject_code} — {entry.subject_name}
          </div>
          {!entry._inHold && (
            <div>
              <span className="font-medium">Slot:</span>{' '}
              {dayLabel(entry.day_of_week)} P{entry.slot_index + 1} ({entry.start_time}–{entry.end_time})
            </div>
          )}
        </div>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-700">Teacher</label>
            <select
              className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
              value={teacherId}
              onChange={(e) => setTeacherId(e.target.value)}
            >
              {teachers.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.code} — {t.full_name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-700">Room</label>
            <select
              className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
              value={roomId}
              onChange={(e) => setRoomId(e.target.value)}
            >
              {rooms.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.code} — {r.name} ({r.room_type})
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <button className="btn-secondary text-sm" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary text-sm" onClick={() => onSave(teacherId, roomId)}>
            Apply
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export function ManualEditor() {
  const { programCode } = useLayoutContext()

  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState('')
  const [toast, setToast] = React.useState('')
  const [saving, setSaving] = React.useState(false)

  const [sourceRunId, setSourceRunId] = React.useState('')
  const [runStatus, setRunStatus] = React.useState('')
  const [boardEntries, setBoardEntries] = React.useState<BoardEntry[]>([])
  const [originalEntries, setOriginalEntries] = React.useState<ManualEditorEntry[]>([])
  const [slots, setSlots] = React.useState<ManualEditorSlot[]>([])
  const [teachers, setTeachers] = React.useState<ManualEditorTeacher[]>([])
  const [rooms, setRooms] = React.useState<ManualEditorRoom[]>([])
  const [conflicts, setConflicts] = React.useState<ConflictItem[]>([])
  const [editingEntry, setEditingEntry] = React.useState<BoardEntry | null>(null)
  const [activeEntry, setActiveEntry] = React.useState<BoardEntry | null>(null)
  const [runs, setRuns] = React.useState<RunSummary[]>([])

  function showToast(msg: string, ms = 3500) {
    setToast(msg)
    window.setTimeout(() => setToast(''), ms)
  }

  // Load runs for the selector
  React.useEffect(() => {
    if (!programCode) return
    listRuns({ program_code: programCode, limit: 20 })
      .then((rs) =>
        setRuns(rs.filter((r) => ['FEASIBLE', 'OPTIMAL', 'SUBOPTIMAL'].includes(r.status))),
      )
      .catch(() => {})
  }, [programCode])

  async function loadBoard(pc: string, runId?: string) {
    if (!pc) {
      setError('Select a program first.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const data = await getManualEditorBoard({ program_code: pc, run_id: runId })
      const entries: BoardEntry[] = data.entries.map((e) => ({
        ...e,
        _inHold: false,
        _dirty: false,
      }))
      setSourceRunId(data.run_id)
      setRunStatus(data.run_status)
      setBoardEntries(entries)
      setOriginalEntries(data.entries)
      setSlots(data.slots)
      setTeachers(data.teachers)
      setRooms(data.rooms)
      setConflicts(detectConflicts(entries))
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load timetable board.'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  React.useEffect(() => {
    if (programCode) loadBoard(programCode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programCode])

  // ── Derived values ──────────────────────────────────────────────────────────

  const sections = React.useMemo(() => {
    const map = new Map<string, { id: string; code: string; name: string }>()
    for (const e of boardEntries) {
      if (!map.has(e.section_id))
        map.set(e.section_id, { id: e.section_id, code: e.section_code, name: e.section_name })
    }
    return [...map.values()].sort((a, b) => a.code.localeCompare(b.code))
  }, [boardEntries])

  const days = React.useMemo(
    () => [...new Set(slots.map((s) => s.day_of_week))].sort((a, b) => a - b),
    [slots],
  )

  const slotsByDay = React.useMemo(() => {
    const m = new Map<number, ManualEditorSlot[]>()
    for (const s of slots) {
      if (!m.has(s.day_of_week)) m.set(s.day_of_week, [])
      m.get(s.day_of_week)!.push(s)
    }
    for (const arr of m.values()) arr.sort((a, b) => a.slot_index - b.slot_index)
    return m
  }, [slots])

  const slotLookup = React.useMemo(() => {
    const m = new Map<string, ManualEditorSlot>()
    for (const s of slots) m.set(`${s.day_of_week}__${s.slot_index}`, s)
    return m
  }, [slots])

  const entryMap = React.useMemo(() => {
    const m = new Map<string, BoardEntry[]>()
    for (const e of boardEntries) {
      if (e._inHold) continue
      const key = `${e.section_id}__${e.day_of_week}__${e.slot_index}`
      if (!m.has(key)) m.set(key, [])
      m.get(key)!.push(e)
    }
    return m
  }, [boardEntries])

  const holdEntries = React.useMemo(() => boardEntries.filter((e) => e._inHold), [boardEntries])

  const conflictIds = React.useMemo(() => {
    const s = new Set<string>()
    for (const c of conflicts) for (const id of c.entryIds) s.add(id)
    return s
  }, [conflicts])

  const dirtyCount = boardEntries.filter((e) => e._dirty).length

  // ── DnD handlers ───────────────────────────────────────────────────────────

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }))

  function onDragStart(ev: DragStartEvent) {
    const data = ev.active.data.current as BoardEntry | undefined
    setActiveEntry(data ?? null)
  }

  function onDragEnd(ev: DragEndEvent) {
    setActiveEntry(null)
    if (!ev.over) return

    const draggedId = ev.active.id as string
    const drop = ev.over.data.current as DropData | undefined
    if (!drop) return

    setBoardEntries((prev) => {
      const updated = prev.map((e) => {
        if (e.id !== draggedId) return e

        if (drop.type === 'HOLD') {
          return { ...e, _inHold: true, _dirty: true }
        }

        if (drop.type === 'CELL') {
          // Only allow drops to cells matching the entry's own section
          if (drop.section_id !== e.section_id) return e
          const slot = slotLookup.get(`${drop.day_of_week}__${drop.slot_index}`)
          if (!slot) return e
          return {
            ...e,
            _inHold: false,
            _dirty: true,
            slot_id: slot.id,
            day_of_week: slot.day_of_week,
            slot_index: slot.slot_index,
            start_time: slot.start_time,
            end_time: slot.end_time,
          }
        }

        return e
      })
      setConflicts(detectConflicts(updated))
      return updated
    })
  }

  function onDragCancel() {
    setActiveEntry(null)
  }

  // ── User actions ────────────────────────────────────────────────────────────

  function applyEdit(teacherId: string, roomId: string) {
    if (!editingEntry) return
    const teacher = teachers.find((t) => t.id === teacherId)
    const room = rooms.find((r) => r.id === roomId)
    if (!teacher || !room) return

    setBoardEntries((prev) => {
      const updated = prev.map((e) => {
        if (e.id !== editingEntry.id) return e
        return {
          ...e,
          teacher_id: teacher.id,
          teacher_code: teacher.code,
          teacher_name: teacher.full_name,
          room_id: room.id,
          room_code: room.code,
          room_name: room.name,
          _dirty: true,
        }
      })
      setConflicts(detectConflicts(updated))
      return updated
    })
    setEditingEntry(null)
  }

  function handleReset() {
    const entries: BoardEntry[] = originalEntries.map((e) => ({
      ...e,
      _inHold: false,
      _dirty: false,
    }))
    setBoardEntries(entries)
    setConflicts(detectConflicts(entries))
  }

  async function handleSave() {
    const gridEntries = boardEntries.filter((e) => !e._inHold)
    if (gridEntries.length === 0) {
      showToast('Nothing to save — all entries are in hold.')
      return
    }
    setSaving(true)
    try {
      const result = await saveManualEdits({
        source_run_id: sourceRunId,
        program_code: programCode,
        entries: gridEntries.map((e) => ({
          section_id: e.section_id,
          subject_id: e.subject_id,
          teacher_id: e.teacher_id,
          room_id: e.room_id,
          slot_id: e.slot_id,
          combined_class_id: e.combined_class_id,
          elective_block_id: e.elective_block_id,
        })),
      })
      showToast(
        `Saved as run ${result.run_id.split('-')[0]} (${result.entries_written} entries)`,
        5000,
      )
      await loadBoard(programCode, result.run_id)
      // Refresh run list
      listRuns({ program_code: programCode, limit: 20 })
        .then((rs) =>
          setRuns(rs.filter((r) => ['FEASIBLE', 'OPTIMAL', 'SUBOPTIMAL'].includes(r.status))),
        )
        .catch(() => {})
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Save failed.'
      showToast(msg)
    } finally {
      setSaving(false)
    }
  }

  async function handleRunChange(runId: string) {
    if (runId && programCode) await loadBoard(programCode, runId)
  }

  // ── JSX ─────────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col overflow-hidden">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="shrink-0 flex flex-wrap items-center gap-2 border-b bg-white px-4 py-2.5 shadow-sm">
        <span className="font-semibold text-slate-900">Manual Timetable Editor</span>

        {programCode && (
          <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
            {programCode}
          </span>
        )}

        {sourceRunId && (
          <span className="font-mono text-xs text-slate-400">
            {sourceRunId.split('-')[0]} · {runStatus}
          </span>
        )}

        {dirtyCount > 0 && (
          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
            {dirtyCount} edited
          </span>
        )}

        {holdEntries.length > 0 && (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
            {holdEntries.length} in hold
          </span>
        )}

        <div className="flex-1" />

        {runs.length > 1 && (
          <select
            className="rounded-lg border border-slate-300 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-400"
            defaultValue={sourceRunId}
            onChange={(e) => handleRunChange(e.target.value)}
          >
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.id.split('-')[0]} — {r.status}
                {(r.parameters?.scope as string) === 'MANUAL_EDIT' ? ' (manual)' : ''}
              </option>
            ))}
          </select>
        )}

        <button
          className="btn-secondary text-sm"
          onClick={handleReset}
          disabled={loading || saving}
        >
          Reset
        </button>
        <button
          className="btn-primary text-sm"
          onClick={handleSave}
          disabled={loading || saving || boardEntries.length === 0}
        >
          {saving ? 'Saving…' : 'Save as new run'}
        </button>
        <Link className="btn-secondary text-sm" to="/dashboard">
          ← Dashboard
        </Link>
      </div>

      {/* ── Toast ──────────────────────────────────────────────────────────── */}
      {toast && (
        <div className="shrink-0 border-b border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">
          {toast}
        </div>
      )}

      {/* ── Error ──────────────────────────────────────────────────────────── */}
      {error && (
        <div className="shrink-0 border-b border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-800">
          {error}
        </div>
      )}

      {/* ── Loading ────────────────────────────────────────────────────────── */}
      {loading && (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-500">
          Loading timetable…
        </div>
      )}

      {/* ── Empty state ────────────────────────────────────────────────────── */}
      {!loading && !error && boardEntries.length === 0 && (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-slate-500">
          <div>No timetable data found.</div>
          <div className="text-xs">Generate a timetable first, then return here to edit it.</div>
          <Link className="btn-primary text-sm" to="/generate">
            Go to Generate
          </Link>
        </div>
      )}

      {/* ── Board ──────────────────────────────────────────────────────────── */}
      {!loading && boardEntries.length > 0 && (
        <DndContext
          sensors={sensors}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          onDragCancel={onDragCancel}
        >
          <div className="flex flex-1 overflow-hidden">
            {/* Grid */}
            <div className="flex-1 overflow-auto">
              <table className="border-collapse text-xs" style={{ minWidth: 'max-content' }}>
                <thead>
                  {/* Day names row */}
                  <tr>
                    <th
                      className="sticky left-0 z-20 min-w-[92px] border border-slate-300 bg-slate-100 px-2 py-1.5 text-left font-semibold text-slate-700"
                      rowSpan={2}
                    >
                      Section
                    </th>
                    {days.map((day) => (
                      <th
                        key={day}
                        className="border border-slate-300 bg-slate-100 px-2 py-1.5 text-center font-semibold text-slate-700"
                        colSpan={slotsByDay.get(day)?.length ?? 1}
                      >
                        {DAY_LABELS[day] ?? `Day ${day}`}
                      </th>
                    ))}
                  </tr>
                  {/* Slot times row */}
                  <tr>
                    {days.map((day) =>
                      (slotsByDay.get(day) ?? []).map((slot) => (
                        <th
                          key={slot.id}
                          className="border border-slate-200 bg-slate-50 px-1 py-1 text-center font-medium text-slate-500"
                          style={{ minWidth: 84, width: 84 }}
                        >
                          <div>P{slot.slot_index + 1}</div>
                          <div className="font-normal text-[10px] text-slate-400">
                            {slot.start_time}
                          </div>
                        </th>
                      )),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {sections.map((sec) => (
                    <tr key={sec.id}>
                      <td className="sticky left-0 z-10 border border-slate-300 bg-white px-2 py-1.5 font-medium text-slate-700 whitespace-nowrap">
                        <div>{sec.code}</div>
                        <div className="max-w-[80px] truncate text-[10px] font-normal text-slate-400">
                          {sec.name}
                        </div>
                      </td>
                      {days.map((day) =>
                        (slotsByDay.get(day) ?? []).map((slot) => {
                          const cellEntries =
                            entryMap.get(`${sec.id}__${day}__${slot.slot_index}`) ?? []
                          const cellData: CellData = {
                            type: 'CELL',
                            section_id: sec.id,
                            day_of_week: day,
                            slot_index: slot.slot_index,
                            slot_id: slot.id,
                          }
                          return (
                            <DroppableCell
                              key={`${sec.id}-${slot.id}`}
                              id={`CELL__${sec.id}__${day}__${slot.slot_index}`}
                              data={cellData}
                            >
                              {cellEntries.map((e) => (
                                <DraggableCard
                                  key={e.id}
                                  entry={e}
                                  conflictIds={conflictIds}
                                  onEdit={setEditingEntry}
                                />
                              ))}
                            </DroppableCell>
                          )
                        }),
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Right panel */}
            <div className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto border-l bg-slate-50 p-3">
              {/* Hold area */}
              <div>
                <div className="mb-1.5 text-xs font-semibold text-slate-700">
                  Hold Area{' '}
                  {holdEntries.length > 0 && (
                    <span className="text-slate-400">({holdEntries.length})</span>
                  )}
                </div>
                <DroppableHold>
                  {holdEntries.length === 0 ? (
                    <div className="w-full py-4 text-center text-[11px] text-slate-400">
                      Drag cards here to remove from schedule
                    </div>
                  ) : (
                    holdEntries.map((e) => (
                      <DraggableCard
                        key={e.id}
                        entry={e}
                        conflictIds={conflictIds}
                        onEdit={setEditingEntry}
                      />
                    ))
                  )}
                </DroppableHold>
              </div>

              {/* Conflicts */}
              <div>
                <div className="mb-1.5 text-xs font-semibold text-slate-700">
                  Conflicts{' '}
                  {conflicts.length === 0 ? (
                    <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-emerald-700">
                      0
                    </span>
                  ) : (
                    <span className="rounded-full bg-rose-100 px-1.5 py-0.5 text-rose-700">
                      {conflicts.length}
                    </span>
                  )}
                </div>
                {conflicts.length === 0 ? (
                  <div className="rounded-lg bg-emerald-50 px-2 py-2 text-[11px] text-emerald-700">
                    No conflicts detected ✓
                  </div>
                ) : (
                  <div className="space-y-1">
                    {conflicts.map((c, i) => (
                      <div
                        key={i}
                        className={[
                          'rounded-lg px-2 py-1.5 text-[11px] leading-snug',
                          c.type === 'TEACHER'
                            ? 'bg-rose-50 text-rose-800 ring-1 ring-rose-200'
                            : 'bg-amber-50 text-amber-800 ring-1 ring-amber-200',
                        ].join(' ')}
                      >
                        <span className="font-semibold">{c.type}</span> — {c.message}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Legend */}
              <div className="mt-auto space-y-1 border-t border-slate-200 pt-2 text-[10px] text-slate-400">
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded bg-indigo-50 ring-1 ring-indigo-200" />
                  Normal
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded bg-amber-50 ring-1 ring-amber-400" />
                  Edited
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded bg-rose-100 ring-1 ring-rose-400" />
                  Conflict
                </div>
                <div className="mt-1.5 text-slate-400">
                  Drag cards to reschedule. Click a card to edit teacher or room.
                </div>
              </div>
            </div>
          </div>

          {/* Drag overlay – follows cursor */}
          <DragOverlay dropAnimation={null}>
            {activeEntry ? <CardPreview entry={activeEntry} /> : null}
          </DragOverlay>
        </DndContext>
      )}

      {/* Edit modal */}
      {editingEntry && (
        <EditModal
          entry={editingEntry}
          teachers={teachers}
          rooms={rooms}
          onSave={applyEdit}
          onClose={() => setEditingEntry(null)}
        />
      )}
    </div>
  )
}
