import React from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Toast } from '../components/Toast'
import { useLayoutContext } from '../components/Layout'
import { PremiumSelect } from '../components/PremiumSelect'
import { clearTimetables, deleteTimetableRun } from '../api/admin'
import {
  listRunEntries,
  listRuns,
  listTimeSlots,
  listFixedEntries,
  listSpecialAllotments,
  upsertFixedEntry,
  deleteFixedEntry,
  listSectionRequiredSubjects,
  getAssignedTeacher,
  type RunSummary,
  type TimeSlot,
  type TimetableEntry,
  type FixedTimetableEntry,
  type SpecialAllotment,
  type RequiredSubject,
  type AssignedTeacher,
} from '../api/solver'
import { listRooms, type Room } from '../api/rooms'
import { listSections, type Section } from '../api/sections'
import { listTeachers, type Teacher } from '../api/teachers'
import {
  getRoomTimetable,
  getFacultyTimetable,
  type TimetableGridEntry,
} from '../api/timetable'
import { useModalScrollLock } from '../hooks/useModalScrollLock'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

type CellKey = string

function cellKey(day: number, slotIndex: number): CellKey {
  return `${day}:${slotIndex}`
}

function fmtSlotLabel(s: TimeSlot) {
  const d = WEEKDAYS[s.day_of_week] ?? `D${s.day_of_week}`
  return `${d} #${s.slot_index} (${s.start_time}-${s.end_time})`
}

type TimetableView = 'SECTION' | 'ROOM' | 'FACULTY'

function viewLabel(v: TimetableView) {
  if (v === 'SECTION') return 'Section Timetable'
  if (v === 'ROOM') return 'Room Timetable'
  return 'Faculty Timetable'
}

function toGridCellMap(items: TimetableGridEntry[]) {
  const map = new Map<CellKey, TimetableGridEntry[]>()
  for (const e of items) {
    const k = cellKey(e.day, e.slot_index)
    const arr = map.get(k) ?? []
    arr.push(e)
    map.set(k, arr)
  }
  for (const [k, arr] of map.entries()) {
    arr.sort((a, b) => {
      const aKey = `${a.year_number}-${a.section_code}-${a.subject_code}`
      const bKey = `${b.year_number}-${b.section_code}-${b.subject_code}`
      return aKey.localeCompare(bKey)
    })
    map.set(k, arr)
  }
  return map
}

function groupGridForCell(entries: TimetableGridEntry[]) {
  const nonElective: TimetableGridEntry[] = []
  const electiveByBlock = new Map<string, { name: string; items: TimetableGridEntry[] }>()

  for (const e of entries) {
    const blockId = e.elective_block_id ?? null
    if (!blockId) {
      nonElective.push(e)
      continue
    }
    const name = String(e.elective_block_name ?? 'Elective Block')
    const group = electiveByBlock.get(blockId) ?? { name, items: [] }
    group.items.push(e)
    electiveByBlock.set(blockId, group)
  }

  const electiveGroups = Array.from(electiveByBlock.entries())
    .sort((a, b) => a[1].name.localeCompare(b[1].name))
    .map(([blockId, g]) => ({ blockId, name: g.name, items: g.items }))

  nonElective.sort((a, b) => {
    const aKey = `${a.year_number}-${a.section_code}-${a.subject_code}`
    const bKey = `${b.year_number}-${b.section_code}-${b.subject_code}`
    return aKey.localeCompare(bKey)
  })
  for (const g of electiveGroups) {
    g.items.sort((a, b) => {
      const aKey = `${a.year_number}-${a.section_code}-${a.subject_code}`
      const bKey = `${b.year_number}-${b.section_code}-${b.subject_code}`
      return aKey.localeCompare(bKey)
    })
  }

  return { nonElective, electiveGroups }
}

type CollapsedGridEntry = TimetableGridEntry & { section_codes: string[] }

function collapseCombinedGridEntries(items: TimetableGridEntry[]): CollapsedGridEntry[] {
  const byKey = new Map<string, CollapsedGridEntry>()

  for (const e of items) {
    const key = [e.subject_code, e.room_code, String(e.year_number), String(e.elective_block_id ?? '')].join('|')
    const existing = byKey.get(key)
    if (!existing) {
      byKey.set(key, { ...e, section_codes: [e.section_code] })
      continue
    }
    if (!existing.section_codes.includes(e.section_code)) {
      existing.section_codes.push(e.section_code)
    }
  }

  const collapsed = Array.from(byKey.values())
  for (const e of collapsed) e.section_codes.sort((a, b) => a.localeCompare(b))
  collapsed.sort((a, b) => {
    const aKey = `${a.year_number}-${a.section_codes.join(',')}-${a.subject_code}`
    const bKey = `${b.year_number}-${b.section_codes.join(',')}-${b.subject_code}`
    return aKey.localeCompare(bKey)
  })
  return collapsed
}

function groupSectionCellEntries(items: TimetableEntry[]) {
  const nonElective: TimetableEntry[] = []
  const byBlock = new Map<string, { name: string; entries: TimetableEntry[] }>()

  for (const e of items) {
    const blockId = (e as any).elective_block_id as string | undefined
    if (!blockId) {
      nonElective.push(e)
      continue
    }
    const name = String((e as any).elective_block_name ?? 'Elective Block')
    const g = byBlock.get(blockId) ?? { name, entries: [] }
    g.entries.push(e)
    byBlock.set(blockId, g)
  }

  const blocks = Array.from(byBlock.entries())
    .sort((a, b) => a[1].name.localeCompare(b[1].name))
    .map(([blockId, g]) => ({ blockId, name: g.name, entries: g.entries }))

  nonElective.sort((a, b) => `${a.subject_code}-${a.teacher_code}`.localeCompare(`${b.subject_code}-${b.teacher_code}`))
  for (const b of blocks) {
    b.entries.sort((a, b2) => `${a.subject_code}-${a.teacher_code}`.localeCompare(`${b2.subject_code}-${b2.teacher_code}`))
  }

  return { blocks, nonElective }
}

function EntryTooltip({ lines }: { lines: string[] }) {
  return (
    <div className="pointer-events-none absolute left-0 top-full z-20 mt-1 hidden w-72 rounded-xl bg-slate-900 px-3 py-2 text-xs text-white shadow-lg group-hover:block print:hidden">
      {lines.map((x, i) => (
        <div key={i} className={i === 0 ? 'font-semibold' : 'text-white/90'}>
          {x}
        </div>
      ))}
    </div>
  )
}

function yearFromSectionCode(code: string): number | null {
  const m = /^Y(\d+)\b/i.exec(String(code ?? '').trim())
  if (!m) return null
  const n = Number(m[1])
  return Number.isFinite(n) ? n : null
}

export function Timetable() {
  const { programCode, academicYearNumber } = useLayoutContext()
  const [params, setParams] = useSearchParams()

  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)

  const [runs, setRuns] = React.useState<RunSummary[]>([])
  const [slots, setSlots] = React.useState<TimeSlot[]>([])
  const [entries, setEntries] = React.useState<TimetableEntry[]>([])

  const [sections, setSections] = React.useState<Section[]>([])
  const [fixedEntries, setFixedEntries] = React.useState<FixedTimetableEntry[]>([])
  const [specialAllotments, setSpecialAllotments] = React.useState<SpecialAllotment[]>([])
  const [requiredSubjects, setRequiredSubjects] = React.useState<RequiredSubject[]>([])
  const [rooms, setRooms] = React.useState<Room[]>([])
  const [teachers, setTeachers] = React.useState<Teacher[]>([])
  const [showAllRuns, setShowAllRuns] = React.useState(false)
  const [runScopeFilter, setRunScopeFilter] = React.useState<
    'ALL' | 'PROGRAM_GLOBAL' | 'YEAR_ONLY'
  >('PROGRAM_GLOBAL')

  const view = (params.get('view') as TimetableView | null) ?? 'SECTION'

  const runId = params.get('runId') ?? ''
  const sectionCode = params.get('section') ?? ''

  const [roomId, setRoomId] = React.useState('')
  const [facultyId, setFacultyId] = React.useState('')
  const [viewLoading, setViewLoading] = React.useState(false)
  const [roomGrid, setRoomGrid] = React.useState<TimetableGridEntry[]>([])
  const [facultyGrid, setFacultyGrid] = React.useState<TimetableGridEntry[]>([])

  const selectedSectionId = React.useMemo(() => {
    if (!sectionCode) return ''
    const fromEntries = entries.find((e) => e.section_code === sectionCode)?.section_id
    if (fromEntries) return fromEntries
    return sections.find((s) => s.code === sectionCode)?.id ?? ''
  }, [entries, sections, sectionCode])

  const selectedRun = React.useMemo(() => {
    return runs.find((r) => r.id === runId) ?? null
  }, [runs, runId])

  const runHasEntries =
    selectedRun?.status === 'FEASIBLE' || selectedRun?.status === 'SUBOPTIMAL' || selectedRun?.status === 'OPTIMAL'

  const roomById = React.useMemo(() => {
    const m = new Map<string, Room>()
    for (const r of rooms) m.set(r.id, r)
    return m
  }, [rooms])

  const roomByCode = React.useMemo(() => {
    const m = new Map<string, Room>()
    for (const r of rooms) m.set(r.code, r)
    return m
  }, [rooms])

  function fmtRoomCodeById(roomId: string, fallbackCode: string): string {
    const r = roomById.get(roomId)
    return r?.is_special ? `🔒 ${fallbackCode}` : fallbackCode
  }

  function fmtRoomCodeByCode(roomCode: string): string {
    const r = roomByCode.get(roomCode)
    return r?.is_special ? `🔒 ${roomCode}` : roomCode
  }

  function runTag(r: RunSummary): string {
    const scope = String((r as any).parameters?.scope ?? '')
    if (scope === 'PROGRAM_GLOBAL') return 'GLOBAL'
    const year = (r as any).parameters?.academic_year_number
    if (year != null) return `YEAR ${year}`
    return 'LEGACY'
  }

  const scopeFilteredRuns = React.useMemo(() => {
    return runs.filter((r) => {
      const scope = String((r as any).parameters?.scope ?? '')
      if (runScopeFilter === 'ALL') return true
      if (runScopeFilter === 'PROGRAM_GLOBAL') return scope === 'PROGRAM_GLOBAL'
      if (runScopeFilter === 'YEAR_ONLY') {
        const year = (r as any).parameters?.academic_year_number
        return year != null && Number(year) === Number(academicYearNumber)
      }
      return true
    })
  }, [runs, runScopeFilter, academicYearNumber])

  const filteredRuns = React.useMemo(() => {
    const base = scopeFilteredRuns
    if (showAllRuns) return base
    return base.filter((r) => r.status === 'FEASIBLE' || r.status === 'SUBOPTIMAL' || r.status === 'OPTIMAL')
  }, [scopeFilteredRuns, showAllRuns])

  const runsForSelect = React.useMemo(() => {
    // Keep the current selection in the list even when filtered,
    // so the select doesn't appear blank.
    if (!selectedRun) return filteredRuns
    if (filteredRuns.some((r) => r.id === selectedRun.id)) return filteredRuns
    return [selectedRun, ...filteredRuns]
  }, [filteredRuns, selectedRun])

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  async function refreshRooms() {
    try {
      const data = await listRooms()
      setRooms(data.filter((r) => Boolean(r.is_active)))
    } catch (e: any) {
      showToast(`Rooms load failed: ${String(e?.message ?? e)}`, 3500)
    }
  }

  async function refreshTeachers() {
    try {
      const data = await listTeachers()
      setTeachers(data.filter((t) => Boolean(t.is_active)))
    } catch (e: any) {
      showToast(`Teachers load failed: ${String(e?.message ?? e)}`, 3500)
    }
  }

  async function refreshFixedData(sectionId: string) {
    if (!sectionId) return
    try {
      const [fe, sa, subj] = await Promise.all([
        listFixedEntries({ section_id: sectionId }),
        listSpecialAllotments({ section_id: sectionId }),
        listSectionRequiredSubjects({ section_id: sectionId }),
      ])
      setFixedEntries(fe)
      setSpecialAllotments(sa)
      setRequiredSubjects(subj.filter((s) => Boolean(s.is_active)))
    } catch (e: any) {
      showToast(`Fixed slots load failed: ${String(e?.message ?? e)}`, 3500)
    }
  }

  async function onDeleteThisRun() {
    if (!runId) return
    const ok = window.confirm(
      'Delete this timetable run and its entries? This cannot be undone.',
    )
    if (!ok) return

    setLoading(true)
    try {
      const result = await deleteTimetableRun({ confirm: 'DELETE', run_id: runId })
      if (result.ok) {
        showToast('Deleted timetable run')
        const p = new URLSearchParams(params)
        p.delete('runId')
        p.delete('section')
        setParams(p, { replace: true })
        setEntries([])
      } else {
        showToast(result.message || 'Delete failed', 3500)
      }
      await refreshRunsAndSlots()
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function onDeleteAllTimetables() {
    const ok = window.confirm(
      'Delete ALL timetable runs/entries? This cannot be undone.',
    )
    if (!ok) return

    setLoading(true)
    try {
        const result = await clearTimetables({ confirm: 'DELETE', academic_year_number: academicYearNumber })
      if (result.ok) {
        showToast('Deleted all timetables')
        const p = new URLSearchParams(params)
        p.delete('runId')
        p.delete('section')
        setParams(p, { replace: true })
        setEntries([])
      } else {
        showToast(result.message || 'Delete failed', 3500)
      }
      await refreshRunsAndSlots()
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function refreshRunsAndSlots() {
    setLoading(true)
    try {
      const pc = programCode.trim()
      if (!pc) {
        const s = await listTimeSlots()
        setRuns([])
        setSlots(s)
        setSections([])
        setEntries([])

        const p = new URLSearchParams(params)
        p.delete('runId')
        p.delete('section')
        setParams(p, { replace: true })
        return
      }
      const [r, s, sec] = await Promise.all([
        listRuns({ program_code: pc, limit: 50 }),
        listTimeSlots(),
        listSections({ program_code: pc, academic_year_number: academicYearNumber }),
      ])
      setRuns(r)
      setSlots(s)
      setSections(sec.filter((x) => Boolean(x.is_active)))

      if (!runId && r.length > 0) {
        const preferred =
          r.find(
            (x) =>
              String((x as any).parameters?.scope ?? '') === 'PROGRAM_GLOBAL' &&
              (x.status === 'OPTIMAL' || x.status === 'FEASIBLE' || x.status === 'SUBOPTIMAL'),
          ) ??
          r.find((x) => x.status === 'OPTIMAL' || x.status === 'FEASIBLE' || x.status === 'SUBOPTIMAL') ??
          r[0]
        const p = new URLSearchParams(params)
        p.set('runId', preferred.id)
        p.delete('section')
        setParams(p, { replace: true })
      }
    } catch (e: any) {
      showToast(`Load failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function loadEntriesForRun(run: string) {
    if (!run) return
    setLoading(true)
    try {
      const data = await listRunEntries(run)
      setEntries(data)

      if (!sectionCode) {
        const uniqueSections = Array.from(new Set(data.map((e) => e.section_code))).sort()
        if (uniqueSections.length > 0) {
          params.set('section', uniqueSections[0])
          setParams(params, { replace: true })
        }
      }
    } catch (e: any) {
      showToast(`Load entries failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  React.useEffect(() => {
    refreshRunsAndSlots()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programCode, academicYearNumber])

  React.useEffect(() => {
    refreshRooms()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  React.useEffect(() => {
    refreshTeachers()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  React.useEffect(() => {
    if (runId) loadEntriesForRun(runId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  React.useEffect(() => {
    if (!roomId && rooms.length > 0) setRoomId(rooms[0].id)
  }, [rooms, roomId])

  React.useEffect(() => {
    if (!facultyId && teachers.length > 0) setFacultyId(teachers[0].id)
  }, [teachers, facultyId])

  React.useEffect(() => {
    async function loadRoomGrid() {
      if (!roomId) return
      setViewLoading(true)
      try {
        const data = await getRoomTimetable(roomId, runId || undefined)
        setRoomGrid(data)
      } catch (e: any) {
        showToast(`Room timetable load failed: ${String(e?.message ?? e)}`, 3500)
      } finally {
        setViewLoading(false)
      }
    }

    if (view === 'ROOM') loadRoomGrid()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, roomId, runId])

  React.useEffect(() => {
    async function loadFacultyGrid() {
      if (!facultyId) return
      setViewLoading(true)
      try {
        const data = await getFacultyTimetable(facultyId, runId || undefined)
        setFacultyGrid(data)
      } catch (e: any) {
        showToast(`Faculty timetable load failed: ${String(e?.message ?? e)}`, 3500)
      } finally {
        setViewLoading(false)
      }
    }

    if (view === 'FACULTY') loadFacultyGrid()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, facultyId, runId])

  React.useEffect(() => {
    if (!selectedSectionId) return
    refreshFixedData(selectedSectionId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSectionId])

  const sectionCodes = React.useMemo(() => {
    return Array.from(new Set(entries.map((e) => e.section_code))).sort()
  }, [entries])

  const sectionCodesForYear = React.useMemo(() => {
    const yn = Number(academicYearNumber)
    return sectionCodes.filter((c) => {
      const y = yearFromSectionCode(c)
      return y == null || y === yn
    })
  }, [sectionCodes, academicYearNumber])

  const sectionsForYear = React.useMemo(() => {
    const yn = Number(academicYearNumber)
    return sections.filter((s) => {
      const y = yearFromSectionCode(s.code)
      return y == null || y === yn
    })
  }, [sections, academicYearNumber])

  React.useEffect(() => {
    if (view !== 'SECTION') return
    const options = runHasEntries
      ? sectionCodesForYear
      : sectionsForYear
          .slice()
          .sort((a, b) => a.code.localeCompare(b.code))
          .map((s) => s.code)

    if (!options.length) return
    if (!sectionCode) {
      setSection(options[0])
      return
    }
    if (!options.includes(sectionCode)) {
      setSection(options[0])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, runHasEntries, sectionCodesForYear, sectionsForYear, academicYearNumber])

  const slotsByDay = React.useMemo(() => {
    const map = new Map<number, TimeSlot[]>()
    for (const s of slots) {
      const arr = map.get(s.day_of_week) ?? []
      arr.push(s)
      map.set(s.day_of_week, arr)
    }
    for (const [d, arr] of map.entries()) {
      arr.sort((a, b) => a.slot_index - b.slot_index)
      map.set(d, arr)
    }
    return map
  }, [slots])

  const days = React.useMemo(() => {
    return Array.from(slotsByDay.keys()).sort((a, b) => a - b)
  }, [slotsByDay])

  const maxSlotIndex = React.useMemo(() => {
    let max = -1
    for (const s of slots) max = Math.max(max, s.slot_index)
    return max
  }, [slots])

  const slotIndices = React.useMemo(() => {
    const set = new Set<number>()
    for (const s of slots) set.add(s.slot_index)
    return Array.from(set).sort((a, b) => a - b)
  }, [slots])

  const sectionEntries = React.useMemo(() => {
    if (!sectionCode) return []
    return entries.filter((e) => e.section_code === sectionCode)
  }, [entries, sectionCode])

  const byCell = React.useMemo(() => {
    const map = new Map<CellKey, TimetableEntry[]>()
    for (const e of sectionEntries) {
      const key = cellKey(e.day_of_week, e.slot_index)
      const arr = map.get(key) ?? []
      arr.push(e)
      map.set(key, arr)
    }
    for (const [k, arr] of map.entries()) {
      arr.sort((a, b) => a.subject_code.localeCompare(b.subject_code))
      map.set(k, arr)
    }
    return map
  }, [sectionEntries])

  const fixedByCell = React.useMemo(() => {
    const subjById = new Map(requiredSubjects.map((s) => [s.id, s]))
    const map = new Map<CellKey, { entry: FixedTimetableEntry; isStart: boolean }>()
    for (const e of fixedEntries.filter((x) => x.is_active)) {
      const baseKey = cellKey(e.day_of_week, e.slot_index)
      map.set(baseKey, { entry: e, isStart: true })

      if (String(e.subject_type) === 'LAB') {
        const subj = subjById.get(e.subject_id)
        const block = Number(subj?.lab_block_size_slots ?? 1)
        if (block > 1) {
          for (let j = 1; j < block; j++) {
            map.set(cellKey(e.day_of_week, e.slot_index + j), { entry: e, isStart: false })
          }
        }
      }
    }
    return map
  }, [fixedEntries, requiredSubjects])

  const specialByCell = React.useMemo(() => {
    const subjById = new Map(requiredSubjects.map((s) => [s.id, s]))
    const map = new Map<CellKey, { entry: SpecialAllotment; isStart: boolean }>()
    for (const e of specialAllotments.filter((x) => x.is_active)) {
      const baseKey = cellKey(e.day_of_week, e.slot_index)
      map.set(baseKey, { entry: e, isStart: true })

      if (String(e.subject_type) === 'LAB') {
        const subj = subjById.get(e.subject_id)
        const block = Number(subj?.lab_block_size_slots ?? 1)
        if (block > 1) {
          for (let j = 1; j < block; j++) {
            map.set(cellKey(e.day_of_week, e.slot_index + j), { entry: e, isStart: false })
          }
        }
      }
    }
    return map
  }, [specialAllotments, requiredSubjects])

  const [fixedModalOpen, setFixedModalOpen] = React.useState(false)
  const [fixedModalCell, setFixedModalCell] = React.useState<{ day: number; slotIndex: number; slotId: string } | null>(
    null,
  )
  const [fixedEditingEntry, setFixedEditingEntry] = React.useState<FixedTimetableEntry | null>(null)
  const [fixedAssignedTeacher, setFixedAssignedTeacher] = React.useState<AssignedTeacher | null>(null)
  const [fixedSaving, setFixedSaving] = React.useState(false)
  const [fixedForm, setFixedForm] = React.useState<{ subject_id: string; teacher_id: string; room_id: string }>(
    { subject_id: '', teacher_id: '', room_id: '' },
  )

  useModalScrollLock(fixedModalOpen)

  React.useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setFixedModalOpen(false)
    }
    if (fixedModalOpen) window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [fixedModalOpen])

  async function openFixedModal(day: number, slotIndex: number) {
    if (!selectedSectionId) return
    const slot = slots.find((s) => s.day_of_week === day && s.slot_index === slotIndex)
    if (!slot) return

    const info = fixedByCell.get(cellKey(day, slotIndex)) ?? null
    const entry = info?.entry ?? null

    setFixedModalCell({ day, slotIndex, slotId: entry?.slot_id ?? slot.id })
    setFixedEditingEntry(entry)
    setFixedForm({
      subject_id: entry?.subject_id ?? '',
      teacher_id: entry?.teacher_id ?? '',
      room_id: entry?.room_id ?? '',
    })
    setFixedAssignedTeacher(null)
    setFixedModalOpen(true)

    if (entry?.subject_id) {
      try {
        const assigned = await getAssignedTeacher({ section_id: selectedSectionId, subject_id: entry.subject_id })
        setFixedAssignedTeacher(assigned)
        setFixedForm((f) => ({ ...f, teacher_id: assigned.teacher_id }))
      } catch {
        setFixedAssignedTeacher(null)
        setFixedForm((f) => ({ ...f, teacher_id: '' }))
      }
    }
  }

  async function onFixedSubjectChange(subjectId: string) {
    if (!selectedSectionId) return
    setFixedAssignedTeacher(null)
    setFixedForm((f) => ({ ...f, subject_id: subjectId, teacher_id: '' }))
    if (!subjectId) return
    try {
      const assigned = await getAssignedTeacher({ section_id: selectedSectionId, subject_id: subjectId })
      setFixedAssignedTeacher(assigned)
      setFixedForm((f) => ({ ...f, teacher_id: assigned.teacher_id }))
    } catch (e: any) {
      showToast(`No assigned teacher for this subject/section`, 3500)
    }
  }

  async function onSaveFixedEntry() {
    if (!selectedSectionId || !fixedModalCell) return
    if (!fixedForm.subject_id || !fixedForm.room_id) {
      showToast('Pick subject and room', 2500)
      return
    }
    if (!fixedForm.teacher_id) {
      showToast('No assigned teacher for this subject/section', 3000)
      return
    }

    setFixedSaving(true)
    try {
      await upsertFixedEntry({
        section_id: selectedSectionId,
        subject_id: fixedForm.subject_id,
        teacher_id: fixedForm.teacher_id,
        room_id: fixedForm.room_id,
        slot_id: fixedModalCell.slotId,
      })
      showToast('Saved fixed slot')
      await refreshFixedData(selectedSectionId)
      setFixedModalOpen(false)
    } catch (e: any) {
      showToast(`Save failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setFixedSaving(false)
    }
  }

  async function onDeleteFixedEntry() {
    if (!selectedSectionId || !fixedEditingEntry) return
    const ok = window.confirm('Delete this fixed slot lock?')
    if (!ok) return

    setFixedSaving(true)
    try {
      await deleteFixedEntry(fixedEditingEntry.id)
      showToast('Deleted fixed slot')
      await refreshFixedData(selectedSectionId)
      setFixedModalOpen(false)
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setFixedSaving(false)
    }
  }

  const labSpans = React.useMemo(() => {
    const spanByCell = new Map<CellKey, { colSpan: number; entry: TimetableEntry; endTime: string }>()
    const skipCells = new Set<CellKey>()

    for (const d of days) {
      for (let slotIndex = 0; slotIndex <= maxSlotIndex - 1; slotIndex++) {
        const k1 = cellKey(d, slotIndex)
        const k2 = cellKey(d, slotIndex + 1)
        if (skipCells.has(k1) || skipCells.has(k2)) continue

        const a = byCell.get(k1) ?? []
        const b = byCell.get(k2) ?? []
        if (a.length !== 1 || b.length !== 1) continue

        const e1 = a[0]
        const e2 = b[0]
        if (e1.subject_type !== 'LAB' || e2.subject_type !== 'LAB') continue

        const sameBlock =
          e1.section_id === e2.section_id &&
          e1.subject_id === e2.subject_id &&
          e1.teacher_id === e2.teacher_id &&
          e1.room_id === e2.room_id
        if (!sameBlock) continue

        spanByCell.set(k1, { colSpan: 2, entry: e1, endTime: e2.end_time })
        skipCells.add(k2)
      }
    }

    return { spanByCell, skipCells }
  }, [byCell, days, maxSlotIndex])

  function setRun(next: string) {
    const p = new URLSearchParams(params)
    p.set('runId', next)
    p.delete('section')
    setParams(p, { replace: true })
  }

  function setSection(next: string) {
    const p = new URLSearchParams(params)
    p.set('section', next)
    setParams(p, { replace: true })
  }

  function setView(next: TimetableView) {
    const p = new URLSearchParams(params)
    p.set('view', next)
    setParams(p, { replace: true })
  }

  async function refreshActiveView() {
    if (view === 'ROOM') {
      if (!roomId) return
      setViewLoading(true)
      try {
        const data = await getRoomTimetable(roomId, runId || undefined)
        setRoomGrid(data)
      } catch (e: any) {
        showToast(`Room timetable load failed: ${String(e?.message ?? e)}`, 3500)
      } finally {
        setViewLoading(false)
      }
      return
    }
    if (view === 'FACULTY') {
      if (!facultyId) return
      setViewLoading(true)
      try {
        const data = await getFacultyTimetable(facultyId, runId || undefined)
        setFacultyGrid(data)
      } catch (e: any) {
        showToast(`Faculty timetable load failed: ${String(e?.message ?? e)}`, 3500)
      } finally {
        setViewLoading(false)
      }
    }
  }

  const hasBaseGrid = days.length > 0 && slotIndices.length > 0
  const canRenderSectionGrid = hasBaseGrid && sectionCode && selectedSectionId
  const roomByCell = React.useMemo(() => toGridCellMap(roomGrid), [roomGrid])
  const facultyByCell = React.useMemo(() => toGridCellMap(facultyGrid), [facultyGrid])
  const selectedRoom = React.useMemo(() => rooms.find((r) => r.id === roomId) ?? null, [rooms, roomId])
  const selectedTeacher = React.useMemo(() => teachers.find((t) => t.id === facultyId) ?? null, [teachers, facultyId])
  const weeklyLoad = React.useMemo(() => {
    if (view === 'ROOM') return roomGrid.length
    if (view === 'FACULTY') return facultyGrid.length
    return sectionEntries.length
  }, [view, roomGrid.length, facultyGrid.length, sectionEntries.length])

  return (
    <div className="space-y-6">
      <Toast message={toast} />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-slate-900">{viewLabel(view)}</div>
          <div className="mt-1 text-sm text-slate-600">
            Uses the selected run; global runs include all years.
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-2xl border bg-white p-1 text-sm print:hidden">
            {(['SECTION', 'ROOM', 'FACULTY'] as TimetableView[]).map((v) => (
              <button
                key={v}
                className={
                  'rounded-2xl px-3 py-2 text-sm font-semibold transition ' +
                  (view === v ? 'bg-emerald-600 text-white' : 'text-slate-700 hover:bg-emerald-50')
                }
                onClick={() => setView(v)}
                type="button"
              >
                {v === 'SECTION' ? 'Section' : v === 'ROOM' ? 'Room' : 'Faculty'}
              </button>
            ))}
          </div>

          <div className="inline-flex rounded-2xl border bg-white p-1 text-sm print:hidden">
            <button
              className={
                'rounded-2xl px-3 py-2 text-sm font-semibold transition ' +
                (runId ? 'text-slate-700 hover:bg-emerald-50' : 'text-slate-400')
              }
              disabled={!runId}
              onClick={() => {
                const url = `/timetable/print-all/sections?runId=${encodeURIComponent(runId)}`
                window.open(url, '_blank', 'noopener,noreferrer')
              }}
              type="button"
              title="Print all sections (one page per section)"
            >
              Print all Sections
            </button>
            <button
              className={
                'rounded-2xl px-3 py-2 text-sm font-semibold transition ' +
                (runId ? 'text-slate-700 hover:bg-emerald-50' : 'text-slate-400')
              }
              disabled={!runId}
              onClick={() => {
                const url = `/timetable/print-all/rooms?runId=${encodeURIComponent(runId)}`
                window.open(url, '_blank', 'noopener,noreferrer')
              }}
              type="button"
              title="Print all rooms (one page per room)"
            >
              Print all Rooms
            </button>
            <button
              className={
                'rounded-2xl px-3 py-2 text-sm font-semibold transition ' +
                (runId ? 'text-slate-700 hover:bg-emerald-50' : 'text-slate-400')
              }
              disabled={!runId}
              onClick={() => {
                const url = `/timetable/print-all/faculty?runId=${encodeURIComponent(runId)}`
                window.open(url, '_blank', 'noopener,noreferrer')
              }}
              type="button"
              title="Print all faculty (one page per teacher)"
            >
              Print all Faculty
            </button>
          </div>

          <div className="inline-flex rounded-2xl border bg-white p-1 text-sm print:hidden">
            <button
              className={
                'rounded-2xl px-3 py-2 text-sm font-semibold transition ' +
                (runId ? 'text-indigo-700 hover:bg-indigo-50' : 'text-slate-400')
              }
              disabled={!runId}
              onClick={() => {
                const url = `/print/sections?runId=${encodeURIComponent(runId)}`
                window.open(url, '_blank', 'noopener,noreferrer')
              }}
              type="button"
              title="Official format: all sections (one page per section, A4 landscape)"
            >
              Official: All Sections
            </button>
            <button
              className={
                'rounded-2xl px-3 py-2 text-sm font-semibold transition ' +
                (runId ? 'text-indigo-700 hover:bg-indigo-50' : 'text-slate-400')
              }
              disabled={!runId}
              onClick={() => {
                const url = `/print/rooms?runId=${encodeURIComponent(runId)}`
                window.open(url, '_blank', 'noopener,noreferrer')
              }}
              type="button"
              title="Official format: all rooms (one page per room, A4 landscape)"
            >
              Official: All Rooms
            </button>
            <button
              className={
                'rounded-2xl px-3 py-2 text-sm font-semibold transition ' +
                (runId ? 'text-indigo-700 hover:bg-indigo-50' : 'text-slate-400')
              }
              disabled={!runId}
              onClick={() => {
                const url = `/print/faculty?runId=${encodeURIComponent(runId)}`
                window.open(url, '_blank', 'noopener,noreferrer')
              }}
              type="button"
              title="Official format: all faculty (one page per teacher, A4 landscape)"
            >
              Official: All Faculty
            </button>
          </div>

          <button
            className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
            onClick={refreshRunsAndSlots}
            disabled={loading}
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
          {view === 'SECTION' ? (
            <>
              <button
                className="btn-danger text-sm font-semibold disabled:opacity-50"
                onClick={onDeleteThisRun}
                disabled={loading || !runId}
                title="Delete the currently selected run"
              >
                Delete timetable
              </button>
              <button
                className="btn-danger text-sm font-semibold disabled:opacity-50"
                onClick={onDeleteAllTimetables}
                disabled={loading}
                title="Delete all runs and entries"
              >
                Delete all timetables
              </button>
            </>
          ) : null}
        </div>
      </div>

      <div className="rounded-3xl border bg-white p-5">
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label htmlFor="tt_run" className="text-xs font-medium text-slate-600">Run</label>
            <PremiumSelect
              id="tt_run"
              ariaLabel="Run"
              className="mt-1 text-sm"
              searchable
              searchPlaceholder="Search runs…"
              value={runsForSelect.length === 0 ? '__none__' : runId}
              onValueChange={(v) => {
                if (v === '__none__') return
                setRun(v)
              }}
              options={
                runsForSelect.length === 0
                  ? [{ value: '__none__', label: 'No runs found', disabled: true }]
                  : runsForSelect.map((r) => ({
                      value: r.id,
                      label: `[${runTag(r)}] ${r.status} — ${new Date(r.created_at).toLocaleString()} (${r.id})`,
                    }))
              }
            />

            <div className="mt-2">
              <label htmlFor="tt_scope" className="text-xs font-medium text-slate-600">Scope filter</label>
              <PremiumSelect
                id="tt_scope"
                ariaLabel="Scope filter"
                className="mt-1 text-sm"
                value={runScopeFilter}
                onValueChange={(v) => setRunScopeFilter(v as any)}
                options={[
                  { value: 'PROGRAM_GLOBAL', label: 'Program Global' },
                  { value: 'YEAR_ONLY', label: 'This Year Only' },
                  { value: 'ALL', label: 'All' },
                ]}
              />
            </div>

            <label className="mt-2 inline-flex items-center gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                className="h-4 w-4 accent-emerald-600"
                checked={showAllRuns}
                onChange={(e) => setShowAllRuns(e.target.checked)}
              />
              Show all runs (include ERROR/INFEASIBLE)
            </label>
          </div>

          <div>
            {view === 'SECTION' ? (
              <>
                <label htmlFor="tt_section" className="text-xs font-medium text-slate-600">Section</label>
                <PremiumSelect
                  id="tt_section"
                  ariaLabel="Section"
                  className="mt-1 text-sm"
                  disabled={loading || (runHasEntries ? sectionCodesForYear.length === 0 : sectionsForYear.length === 0)}
                  value={
                    runHasEntries
                      ? sectionCodesForYear.length === 0
                        ? '__none__'
                        : sectionCode
                      : sectionsForYear.length === 0
                        ? '__none__'
                        : sectionCode
                  }
                  onValueChange={(v) => {
                    if (v === '__none__') return
                    setSection(v)
                  }}
                  options={
                    runHasEntries
                      ? sectionCodesForYear.length === 0
                        ? [{ value: '__none__', label: 'No sections in this run', disabled: true }]
                        : sectionCodesForYear.map((c) => ({ value: c, label: c }))
                      : sectionsForYear.length === 0
                        ? [{ value: '__none__', label: 'No sections found (create Sections first)', disabled: true }]
                        : sectionsForYear
                            .slice()
                            .sort((a, b) => a.code.localeCompare(b.code))
                            .map((s) => ({ value: s.code, label: s.code }))
                  }
                />
              </>
            ) : view === 'ROOM' ? (
              <>
                <label htmlFor="tt_room" className="text-xs font-medium text-slate-600">Room</label>
                <PremiumSelect
                  id="tt_room"
                  ariaLabel="Room"
                  className="mt-1 text-sm"
                  disabled={loading || rooms.length === 0}
                  value={rooms.length === 0 ? '__none__' : roomId}
                  onValueChange={(v) => {
                    if (v === '__none__') return
                    setRoomId(v)
                  }}
                  options={
                    rooms.length === 0
                      ? [{ value: '__none__', label: 'No rooms found', disabled: true }]
                      : rooms
                          .slice()
                          .sort((a, b) => a.code.localeCompare(b.code))
                          .map((r) => ({ value: r.id, label: r.code }))
                  }
                />
              </>
            ) : (
              <>
                <label htmlFor="tt_faculty" className="text-xs font-medium text-slate-600">Faculty</label>
                <PremiumSelect
                  id="tt_faculty"
                  ariaLabel="Faculty"
                  className="mt-1 text-sm"
                  disabled={loading || teachers.length === 0}
                  value={teachers.length === 0 ? '__none__' : facultyId}
                  onValueChange={(v) => {
                    if (v === '__none__') return
                    setFacultyId(v)
                  }}
                  options={
                    teachers.length === 0
                      ? [{ value: '__none__', label: 'No teachers found', disabled: true }]
                      : teachers
                          .slice()
                          .sort((a, b) => a.code.localeCompare(b.code))
                          .map((t) => ({ value: t.id, label: `${t.code} — ${t.full_name}` }))
                  }
                />
              </>
            )}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs text-slate-500">
            Weekly load: <span className="font-semibold text-slate-900">{weeklyLoad}</span> slots
          </div>
          {view !== 'SECTION' ? (
            <button
              className="btn-primary text-sm font-semibold disabled:opacity-50"
              disabled={!hasBaseGrid || !runId || viewLoading}
              onClick={() => window.print()}
              type="button"
            >
              Print / Save PDF
            </button>
          ) : null}
        </div>

        {view === 'SECTION' ? (
          <>
            {!runHasEntries && runId ? (
              <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 p-4">
                <div className="text-sm font-semibold text-slate-900">This run has no timetable entries</div>
                <div className="mt-1 text-sm text-slate-700">
                  Status: <span className="font-medium">{selectedRun?.status ?? '—'}</span>. Only{' '}
                  <span className="font-medium">FEASIBLE</span> / <span className="font-medium">OPTIMAL</span> runs produce a section timetable grid.
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                  <Link
                    to={runId ? `/conflicts?runId=${encodeURIComponent(runId)}` : '/conflicts'}
                    className="btn-secondary text-sm font-medium text-slate-800"
                  >
                    View reason in Conflicts
                  </Link>
                  <Link
                    to="/generate"
                    className="btn-primary text-sm font-semibold"
                  >
                    Run Solve again
                  </Link>
                  <div className="text-xs text-slate-600">
                    Tip: try increasing max solve time (e.g. 30–60s).
                  </div>
                </div>
              </div>
            ) : null}

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <div className="text-xs text-slate-500">
                Tip: if the grid is empty, solve first (Generate → Solve), then select the newest run.
              </div>
              <button
                className="rounded-2xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                disabled={!runId || !sectionCode}
                onClick={() => {
                  const url = `/timetable/print?runId=${encodeURIComponent(runId)}&section=${encodeURIComponent(sectionCode)}`
                  window.open(url, '_blank', 'noopener,noreferrer')
                }}
              >
                Print / Save PDF
              </button>
            </div>
          </>
        ) : null}
      </div>

      {view === 'SECTION' && (
        !canRenderSectionGrid ? (
          <div className="rounded-3xl border bg-white p-5">
            <div className="text-sm text-slate-700">No timetable grid to render yet.</div>
            <div className="mt-1 text-xs text-slate-500">
              Requires time slots, a run, and a section.
            </div>
          </div>
        ) : (
          <div className="rounded-3xl border bg-white p-5">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div className="text-xs text-slate-600">
                Fixed slots: click any cell to add/edit a 🔒 lock (enforced as a hard solver constraint).
              </div>
              <button
                className="btn-secondary px-3 py-2 text-xs font-medium text-slate-800 disabled:opacity-50"
                onClick={() => (selectedSectionId ? refreshFixedData(selectedSectionId) : null)}
                disabled={!selectedSectionId || loading}
              >
                Refresh fixed slots
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
                <thead>
                  <tr>
                    <th className="sticky left-0 z-10 bg-emerald-600 px-3 py-2 text-xs font-semibold text-white">
                      Day
                    </th>
                    {slotIndices.map((slotIndex) => {
                      const labelSlot = slots.find((s) => s.slot_index === slotIndex) ?? null
                      return (
                        <th key={slotIndex} className="bg-emerald-600 px-3 py-2 text-xs font-semibold text-white">
                          <div>#{slotIndex}</div>
                          <div className="text-[11px] font-normal text-white/90">
                            {labelSlot ? `${labelSlot.start_time}-${labelSlot.end_time}` : ''}
                          </div>
                        </th>
                      )
                    })}
                  </tr>
                </thead>
                <tbody>
                  {days.map((d) => {
                    const daySlots = slotsByDay.get(d) ?? []
                    const daySlotIndexSet = new Set(daySlots.map((s) => s.slot_index))

                    return (
                      <tr key={d} className="border-t">
                        <td className="sticky left-0 z-10 border-t bg-white px-3 py-2 align-top">
                          <div className="text-xs font-semibold text-slate-900">{WEEKDAYS[d] ?? `Day ${d}`}</div>
                        </td>

                        {slotIndices.map((slotIndex) => {
                          const hasThisSlot = daySlotIndexSet.has(slotIndex)
                          const key = cellKey(d, slotIndex)

                          const fixedInfo = fixedByCell.get(key) ?? null
                          const specialInfo = specialByCell.get(key) ?? null

                          const specialTitle = specialInfo
                            ? [
                                `Special lock: ${specialInfo.entry.subject_code} (${specialInfo.entry.subject_type})`,
                                `${specialInfo.entry.teacher_code} · ${fmtRoomCodeById(specialInfo.entry.room_id, specialInfo.entry.room_code)}`,
                                specialInfo.entry.reason ? `Reason: ${specialInfo.entry.reason}` : null,
                              ]
                                .filter(Boolean)
                                .join('\n')
                            : undefined

                          if (labSpans.skipCells.has(key)) {
                            return null
                          }

                          const labSpan = labSpans.spanByCell.get(key) ?? null
                          const items = byCell.get(key) ?? []
                          const grouped = groupSectionCellEntries(items)

                          return (
                            <td
                              key={`${d}:${slotIndex}`}
                              colSpan={labSpan?.colSpan}
                              title={specialTitle}
                              className={
                                'border-t px-3 py-2 align-top ' +
                                (hasThisSlot
                                  ? specialInfo
                                    ? 'bg-rose-50'
                                    : fixedInfo
                                      ? 'bg-amber-50'
                                      : 'bg-white'
                                  : 'bg-slate-50 text-slate-400')
                              }
                              onClick={() => (hasThisSlot && !specialInfo ? openFixedModal(d, slotIndex) : null)}
                            >
                              {!hasThisSlot ? (
                                <div className="text-xs">—</div>
                              ) : items.length === 0 ? (
                                specialInfo ? (
                                  <div className="rounded-xl border border-rose-200 bg-white p-2">
                                    <div className="text-xs font-semibold text-slate-900">
                                      🔒 {specialInfo.entry.subject_code}{' '}
                                      <span className="text-slate-500">({specialInfo.entry.subject_type})</span>
                                    </div>
                                    <div className="mt-0.5 text-[11px] text-slate-600">
                                      {specialInfo.entry.teacher_code} · {fmtRoomCodeById(specialInfo.entry.room_id, specialInfo.entry.room_code)}
                                    </div>
                                    <div className="mt-0.5 text-[11px] font-medium text-rose-700">Special lock</div>
                                    {!specialInfo.isStart ? (
                                      <div className="mt-0.5 text-[11px] text-slate-500">(lab block continuation)</div>
                                    ) : null}
                                  </div>
                                ) : fixedInfo ? (
                                  <div className="rounded-xl border border-amber-200 bg-white p-2">
                                    <div className="text-xs font-semibold text-slate-900">
                                      🔒 {fixedInfo.entry.subject_code}{' '}
                                      <span className="text-slate-500">({fixedInfo.entry.subject_type})</span>
                                    </div>
                                    <div className="mt-0.5 text-[11px] text-slate-600">
                                      {fixedInfo.entry.teacher_code} · {fmtRoomCodeById(fixedInfo.entry.room_id, fixedInfo.entry.room_code)}
                                    </div>
                                    {!fixedInfo.isStart ? (
                                      <div className="mt-0.5 text-[11px] text-slate-500">(lab block continuation)</div>
                                    ) : null}
                                  </div>
                                ) : (
                                  <div className="inline-flex rounded-full border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700">
                                    FREE
                                  </div>
                                )
                              ) : labSpan ? (
                                <div className="rounded-xl border bg-slate-50 p-2">
                                  <div className="text-xs font-semibold text-slate-900">
                                    {specialInfo || fixedInfo ? '🔒 ' : ''}
                                    {labSpan.entry.subject_code} <span className="text-slate-500">(Lab · 2 hrs)</span>
                                  </div>
                                  <div className="mt-0.5 text-[11px] text-slate-600">
                                    {labSpan.entry.teacher_code} · {fmtRoomCodeById(labSpan.entry.room_id, labSpan.entry.room_code)}
                                  </div>
                                  {specialInfo ? (
                                    <div className="mt-0.5 text-[11px] font-medium text-rose-700">Special lock</div>
                                  ) : fixedInfo ? (
                                    <div className="mt-0.5 text-[11px] font-medium text-amber-700">🔒 Fixed</div>
                                  ) : null}
                                  <div className="mt-0.5 text-[11px] text-slate-500">
                                    {WEEKDAYS[labSpan.entry.day_of_week] ?? `D${labSpan.entry.day_of_week}`} #{labSpan.entry.slot_index} ({labSpan.entry.start_time}-{labSpan.endTime})
                                  </div>
                                </div>
                              ) : (
                                <div className="space-y-2">
                                  {grouped.blocks.map((b) => (
                                    <div key={b.blockId} className="rounded-xl border border-indigo-200 bg-indigo-50 p-2">
                                      <div className="text-xs font-semibold text-slate-900">
                                        {specialInfo || fixedInfo ? '🔒 ' : ''}🎓 {b.name}{' '}
                                        <span className="text-slate-500">({b.entries.length} parallel)</span>
                                      </div>
                                      <div className="mt-1 space-y-0.5">
                                        {b.entries.map((e) => (
                                          <div key={e.id} className="text-[11px] text-slate-700">
                                            {e.subject_code} · {e.teacher_code} · {fmtRoomCodeById(e.room_id, e.room_code)}
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                  ))}

                                  {grouped.nonElective.map((e) => (
                                    <div key={e.id} className="rounded-xl border bg-slate-50 p-2">
                                      <div className="text-xs font-semibold text-slate-900">{e.subject_code}</div>
                                      {specialInfo ? (
                                        <div className="mt-0.5 text-[11px] font-medium text-rose-700">🔒 Special lock</div>
                                      ) : fixedInfo ? (
                                        <div className="mt-0.5 text-[11px] font-medium text-amber-700">🔒 Fixed</div>
                                      ) : null}
                                      <div className="mt-0.5 text-[11px] text-slate-600">
                                        {e.teacher_code} · {fmtRoomCodeById(e.room_id, e.room_code)}
                                      </div>
                                      <div className="mt-0.5 text-[11px] text-slate-500">
                                        {fmtSlotLabel({
                                          id: e.slot_id,
                                          day_of_week: e.day_of_week,
                                          slot_index: e.slot_index,
                                          start_time: e.start_time,
                                          end_time: e.end_time,
                                        })}
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </td>
                          )
                        })}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

          {fixedModalOpen && fixedModalCell ? (
            <div
              className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center z-50 animate-fadeIn p-4"
              onClick={() => setFixedModalOpen(false)}
            >
              <div
                className="w-full max-w-[600px] bg-white/80 backdrop-blur-lg rounded-2xl shadow-2xl p-6 border border-white/40 animate-scaleIn"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="text-lg font-semibold text-slate-900">Fixed Slot 🔒</div>
                    <div className="mt-1 text-xs text-slate-500">
                      {WEEKDAYS[fixedModalCell.day] ?? `Day ${fixedModalCell.day}`} #{fixedModalCell.slotIndex}
                    </div>
                  </div>
                  <button
                    className="btn-secondary text-xs font-medium text-slate-800 disabled:opacity-50"
                    onClick={() => setFixedModalOpen(false)}
                    disabled={fixedSaving}
                    type="button"
                  >
                    Close
                  </button>
                </div>

                <div className="mt-4 grid gap-3">
                  <div>
                    <label htmlFor="fixed_slot_subject" className="text-xs font-medium text-slate-600">
                      Subject
                    </label>
                    <PremiumSelect
                      id="fixed_slot_subject"
                      ariaLabel="Fixed slot subject"
                      className="mt-1 text-sm"
                      disabled={fixedSaving}
                      value={fixedForm.subject_id || '__none__'}
                      onValueChange={(v) => onFixedSubjectChange(v === '__none__' ? '' : v)}
                      options={[
                        { value: '__none__', label: 'Select…' },
                        ...requiredSubjects
                          .slice()
                          .sort((a, b) => a.code.localeCompare(b.code))
                          .map((s) => ({
                            value: s.id,
                            label: `${s.code} — ${s.name} (${s.subject_type})`,
                          })),
                      ]}
                    />
                    <div className="mt-1 text-[11px] text-slate-500">
                      Labs are locked by start slot (contiguous block).
                    </div>
                  </div>

                  <div className="grid gap-3 md:grid-cols-2">
                    <div>
                      <div className="text-xs font-medium text-slate-600">Teacher (auto)</div>
                      <div className="mt-1 w-full rounded-2xl border bg-slate-50 px-3 py-2 text-sm text-slate-800">
                        {fixedAssignedTeacher
                          ? `${fixedAssignedTeacher.teacher_code} — ${fixedAssignedTeacher.teacher_name}`
                          : fixedForm.subject_id
                            ? 'No assignment'
                            : 'Select a subject'}
                      </div>
                    </div>

                    <div>
                      <label htmlFor="fixed_slot_room" className="text-xs font-medium text-slate-600">
                        Room
                      </label>
                      <PremiumSelect
                        id="fixed_slot_room"
                        ariaLabel="Fixed slot room"
                        className="mt-1 text-sm"
                        disabled={fixedSaving}
                        value={fixedForm.room_id || '__none__'}
                        onValueChange={(v) => setFixedForm((f) => ({ ...f, room_id: v === '__none__' ? '' : v }))}
                        options={[
                          { value: '__none__', label: 'Select…' },
                          ...rooms
                            .filter((r) => !Boolean((r as any).is_special))
                            .slice()
                            .sort((a, b) => a.code.localeCompare(b.code))
                            .map((r) => ({
                              value: r.id,
                              label: `${r.code} — ${r.name} (${r.room_type})`,
                            })),
                        ]}
                      />
                    </div>
                  </div>

                  <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                    <div className="text-xs text-slate-500">
                      {fixedEditingEntry
                        ? `Editing existing lock (${fixedEditingEntry.subject_code})`
                        : 'Creating new lock'}
                    </div>
                    <div className="flex items-center gap-2">
                      {fixedEditingEntry ? (
                        <button
                          className="btn-danger px-4 py-2 text-sm font-semibold disabled:opacity-50"
                          onClick={onDeleteFixedEntry}
                          disabled={fixedSaving}
                          type="button"
                        >
                          Delete
                        </button>
                      ) : null}
                      <button
                        className="btn-primary px-4 py-2 text-sm font-semibold disabled:opacity-50"
                        onClick={onSaveFixedEntry}
                        disabled={fixedSaving}
                        type="button"
                      >
                        {fixedSaving ? 'Saving…' : 'Save'}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      ))}

      {view !== 'SECTION' && (
      !hasBaseGrid ? (
        <div className="rounded-3xl border bg-white p-5">
          <div className="text-sm text-slate-700">No timetable grid to render yet.</div>
          <div className="mt-1 text-xs text-slate-500">Requires time slots and a run.</div>
        </div>
      ) : (
        <div className="rounded-3xl border bg-white p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="text-xs text-slate-600">
              {view === 'ROOM'
                ? `Room: ${selectedRoom?.code ?? '—'} (shows all years in this run)`
                : `Faculty: ${selectedTeacher?.code ?? '—'} (shows all years in this run)`}
            </div>
            <button
              className="btn-secondary text-xs font-medium text-slate-800 disabled:opacity-50"
              onClick={refreshActiveView}
              disabled={viewLoading || !runId || (view === 'ROOM' ? !roomId : !facultyId)}
              type="button"
            >
              {viewLoading ? 'Refreshing…' : 'Refresh view'}
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
              <thead>
                <tr>
                  <th className="sticky left-0 z-10 bg-emerald-600 px-3 py-2 text-xs font-semibold text-white">
                    Day
                  </th>
                  {slotIndices.map((slotIndex) => {
                    const labelSlot = slots.find((s) => s.slot_index === slotIndex) ?? null
                    return (
                      <th key={slotIndex} className="bg-emerald-600 px-3 py-2 text-xs font-semibold text-white">
                        <div>#{slotIndex}</div>
                        <div className="text-[11px] font-normal text-white/90">
                          {labelSlot ? `${labelSlot.start_time}-${labelSlot.end_time}` : ''}
                        </div>
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody>
                {days.map((d) => {
                  const daySlots = slotsByDay.get(d) ?? []
                  const daySlotIndexSet = new Set(daySlots.map((s) => s.slot_index))

                  return (
                    <tr key={d} className="border-t">
                      <td className="sticky left-0 z-10 border-t bg-white px-3 py-2 align-top">
                        <div className="text-xs font-semibold text-slate-900">{WEEKDAYS[d] ?? `Day ${d}`}</div>
                      </td>

                      {slotIndices.map((slotIndex) => {
                        const hasThisSlot = daySlotIndexSet.has(slotIndex)
                        const key = cellKey(d, slotIndex)
                        const items = (view === 'ROOM' ? roomByCell.get(key) : facultyByCell.get(key)) ?? []
                        const grouped = groupGridForCell(items)

                        return (
                          <td
                            key={`${d}:${slotIndex}`}
                            className={
                              'border-t px-3 py-2 align-top ' +
                              (hasThisSlot ? (items.length ? 'bg-white' : 'bg-white') : 'bg-slate-50 text-slate-400')
                            }
                          >
                            {!hasThisSlot ? (
                              <div className="text-xs">—</div>
                            ) : items.length === 0 ? (
                              <div className="inline-flex rounded-full border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700">
                                FREE
                              </div>
                            ) : (
                              <div className="space-y-2">
                                {grouped.electiveGroups.map((g) => {
                                  const listItems =
                                    view === 'FACULTY' ? collapseCombinedGridEntries(g.items) : g.items.map((e) => ({ ...e, section_codes: [e.section_code] }))

                                  const lines = [
                                    `ELECTIVE: ${g.name} (${g.items.length} parallel)`,
                                    ...listItems.map((e) =>
                                      view === 'ROOM'
                                        ? `${e.subject_code} — ${e.section_code} (Y${e.year_number}) — ${e.teacher_name}`
                                        : `${e.subject_code} — ${e.section_codes.join(' + ')} (Y${e.year_number}) — ${fmtRoomCodeByCode(e.room_code)}`,
                                    ),
                                  ]

                                  return (
                                    <div
                                      key={g.blockId}
                                      className="group relative rounded-lg border bg-indigo-50 px-2 py-1"
                                      title={`ELECTIVE: ${g.name}`}
                                    >
                                      <div className="text-xs font-semibold text-slate-900">
                                        🎓 {g.name}
                                        <span className="ml-1 text-slate-500">({g.items.length} parallel)</span>
                                      </div>
                                      <div className="mt-0.5 space-y-0.5 text-[11px] text-slate-700">
                                        {listItems.slice(0, 3).map((e, idx) => (
                                          <div key={`${e.section_codes.join('+')}:${e.subject_code}:${idx}`}>
                                            <span className="font-semibold">
                                              {view === 'ROOM' ? e.section_code : e.section_codes.join(' + ')}
                                            </span>
                                            <span className="text-slate-500"> · </span>
                                            <span>{e.subject_code}</span>
                                            <span className="text-slate-500"> · </span>
                                            <span>{view === 'ROOM' ? e.teacher_name : fmtRoomCodeByCode(e.room_code)}</span>
                                            <span className="text-slate-500"> · </span>
                                            <span>Y{e.year_number}</span>
                                          </div>
                                        ))}
                                        {listItems.length > 3 ? (
                                          <div className="text-[11px] text-slate-500">+{listItems.length - 3} more</div>
                                        ) : null}
                                      </div>
                                      <EntryTooltip lines={lines} />
                                    </div>
                                  )
                                })}

                                {(view === 'FACULTY'
                                  ? collapseCombinedGridEntries(grouped.nonElective)
                                  : grouped.nonElective.map((e) => ({ ...e, section_codes: [e.section_code] }))).map((e, idx) => {
                                  const sectionLabel =
                                    view === 'FACULTY' ? e.section_codes.join(' + ') : e.section_code
                                  const title =
                                    view === 'ROOM'
                                      ? `${e.subject_code} — ${e.section_code} (Y${e.year_number}) — ${e.teacher_name}`
                                      : `${e.subject_code} — ${sectionLabel} (Y${e.year_number}) — ${fmtRoomCodeByCode(e.room_code)}`
                                  return (
                                    <div
                                      key={`${e.day}:${e.slot_index}:${sectionLabel}:${e.subject_code}:${idx}`}
                                      className="group relative rounded-xl border bg-emerald-50 p-2"
                                      title={title}
                                    >
                                      <div className="flex flex-wrap items-center justify-between gap-2">
                                        <div className="text-xs font-semibold text-slate-900">{e.subject_code}</div>
                                        <div className="inline-flex rounded-full bg-emerald-600 px-2 py-0.5 text-[11px] font-semibold text-white">
                                          Y{e.year_number}
                                        </div>
                                      </div>
                                      <div className="mt-0.5 text-[11px] text-slate-700">
                                        <span className="font-semibold">{sectionLabel}</span>
                                        <span className="text-slate-500"> · </span>
                                        <span>{view === 'ROOM' ? e.teacher_name : fmtRoomCodeByCode(e.room_code)}</span>
                                      </div>
                                      <div className="mt-0.5 text-[11px] text-slate-500">
                                        {WEEKDAYS[e.day] ?? `D${e.day}`} #{e.slot_index} ({e.start_time}-{e.end_time})
                                      </div>

                                      <EntryTooltip
                                        lines={
                                          view === 'ROOM'
                                            ? [
                                                `${e.subject_code} — ${e.section_code} (Y${e.year_number})`,
                                                `Teacher: ${e.teacher_name}`,
                                                `Room: ${fmtRoomCodeByCode(e.room_code)}`,
                                                `Time: ${WEEKDAYS[e.day] ?? `D${e.day}`} #${e.slot_index} (${e.start_time}-${e.end_time})`,
                                              ]
                                            : [
                                                `${e.subject_code} — ${sectionLabel} (Y${e.year_number})`,
                                                `Sections: ${sectionLabel}`,
                                                `Room: ${fmtRoomCodeByCode(e.room_code)}`,
                                                `Time: ${WEEKDAYS[e.day] ?? `D${e.day}`} #${e.slot_index} (${e.start_time}-${e.end_time})`,
                                              ]
                                        }
                                      />
                                    </div>
                                  )
                                })}
                              </div>
                            )}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  )
}
