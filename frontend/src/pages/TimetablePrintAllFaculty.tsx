import React from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useLayoutContext } from '../components/Layout'
import { listTimeSlots, type TimeSlot } from '../api/solver'
import { listRooms, type Room } from '../api/rooms'
import { listTeachers, type Teacher } from '../api/teachers'
import { getFacultyTimetable, type TimetableGridEntry } from '../api/timetable'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

type CellKey = string

function cellKey(day: number, slotIndex: number): CellKey {
  return `${day}:${slotIndex}`
}

function slotsIndexAndDays(slots: TimeSlot[]) {
  const slotIndices = Array.from(new Set(slots.map((s) => s.slot_index))).sort((a, b) => a - b)
  const days = Array.from(new Set(slots.map((s) => s.day_of_week))).sort((a, b) => a - b)
  const daySlotIndexSet = new Map<number, Set<number>>()
  for (const s of slots) {
    const set = daySlotIndexSet.get(s.day_of_week) ?? new Set<number>()
    set.add(s.slot_index)
    daySlotIndexSet.set(s.day_of_week, set)
  }
  return { slotIndices, days, daySlotIndexSet }
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

  nonElective.sort((a, b) => `${a.year_number}-${a.section_code}-${a.subject_code}`.localeCompare(`${b.year_number}-${b.section_code}-${b.subject_code}`))
  for (const g of electiveGroups) {
    g.items.sort((a, b) => `${a.year_number}-${a.section_code}-${a.subject_code}`.localeCompare(`${b.year_number}-${b.section_code}-${b.subject_code}`))
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

async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, idx: number) => Promise<R>,
  onProgress?: (done: number, total: number) => void,
): Promise<R[]> {
  const results: R[] = new Array(items.length)
  let next = 0
  let done = 0

  async function worker() {
    while (true) {
      const idx = next
      next += 1
      if (idx >= items.length) return
      results[idx] = await fn(items[idx], idx)
      done += 1
      onProgress?.(done, items.length)
    }
  }

  const workers = Array.from({ length: Math.min(limit, items.length) }, () => worker())
  await Promise.all(workers)
  return results
}

function PrintGrid({
  slots,
  title,
  subtitle,
  items,
  fmtRoomCode,
}: {
  slots: TimeSlot[]
  title: string
  subtitle: string
  items: TimetableGridEntry[]
  fmtRoomCode: (roomCode: string) => string
}) {
  const { slotIndices, days, daySlotIndexSet } = React.useMemo(() => slotsIndexAndDays(slots), [slots])
  const byCell = React.useMemo(() => toGridCellMap(items), [items])

  return (
    <div className="break-inside-avoid">
      <div className="mb-3">
        <div className="text-base font-semibold text-slate-900">{title}</div>
        <div className="mt-0.5 text-xs text-slate-500">{subtitle}</div>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead>
            <tr>
              <th className="bg-emerald-600 px-3 py-2 text-xs font-semibold text-white">Day</th>
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
              const set = daySlotIndexSet.get(d) ?? new Set<number>()
              return (
                <tr key={d} className="border-t">
                  <td className="border-t bg-white px-3 py-2 align-top">
                    <div className="text-xs font-semibold text-slate-900">{WEEKDAYS[d] ?? `Day ${d}`}</div>
                  </td>
                  {slotIndices.map((slotIndex) => {
                    const hasThisSlot = set.has(slotIndex)
                    const key = cellKey(d, slotIndex)
                    const cellItems = byCell.get(key) ?? []
                    const grouped = groupGridForCell(cellItems)

                    return (
                      <td
                        key={`${d}:${slotIndex}`}
                        className={
                          'border-t px-3 py-2 align-top ' +
                          (hasThisSlot ? 'bg-white' : 'bg-slate-50 text-slate-400')
                        }
                      >
                        {!hasThisSlot ? (
                          <div className="text-xs">—</div>
                        ) : cellItems.length === 0 ? (
                          <div className="inline-flex rounded-full border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700">
                            FREE
                          </div>
                        ) : (
                          <div className="space-y-2">
                            {grouped.electiveGroups.map((g) => (
                              <div key={g.blockId} className="rounded-xl border bg-indigo-50 p-2">
                                <div className="text-xs font-semibold text-slate-900">ELECTIVE: {g.name}</div>
                                <div className="mt-0.5 space-y-0.5 text-[11px] text-slate-700">
                                  {collapseCombinedGridEntries(g.items).map((e, idx) => (
                                    <div key={`${e.section_codes.join('+')}:${e.subject_code}:${idx}`}>
                                      <span className="font-semibold">{e.section_codes.join(' + ')}</span>
                                      <span className="text-slate-500"> · </span>
                                      <span>{e.subject_code}</span>
                                      <span className="text-slate-500"> · </span>
                                      <span>{fmtRoomCode(e.room_code)}</span>
                                      <span className="text-slate-500"> · </span>
                                      <span>Y{e.year_number}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ))}

                            {collapseCombinedGridEntries(grouped.nonElective).map((e, idx) => (
                              <div key={`${e.section_codes.join('+')}:${e.subject_code}:${idx}`} className="rounded-xl border bg-emerald-50 p-2">
                                <div className="flex items-center justify-between gap-2">
                                  <div className="text-xs font-semibold text-slate-900">{e.subject_code}</div>
                                  <div className="inline-flex rounded-full bg-emerald-600 px-2 py-0.5 text-[11px] font-semibold text-white">
                                    Y{e.year_number}
                                  </div>
                                </div>
                                <div className="mt-0.5 text-[11px] text-slate-700">
                                  <span className="font-semibold">{e.section_codes.join(' + ')}</span>
                                  <span className="text-slate-500"> · </span>
                                  <span>{fmtRoomCode(e.room_code)}</span>
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
    </div>
  )
}

export function TimetablePrintAllFaculty() {
  const { programCode } = useLayoutContext()
  const [params] = useSearchParams()
  const runId = params.get('runId') ?? ''

  const [loading, setLoading] = React.useState(false)
  const [progress, setProgress] = React.useState<{ done: number; total: number }>({ done: 0, total: 0 })
  const [error, setError] = React.useState<string>('')
  const [fetchWarnings, setFetchWarnings] = React.useState<string[]>([])

  const [slots, setSlots] = React.useState<TimeSlot[]>([])
  const [rooms, setRooms] = React.useState<Room[]>([])
  const [teachers, setTeachers] = React.useState<Teacher[]>([])
  const [grids, setGrids] = React.useState<
    Array<{ teacherId: string; teacherCode: string; teacherName: string; entries: TimetableGridEntry[] }>
  >([])
  const [autoPrinted, setAutoPrinted] = React.useState(false)

  const roomByCode = React.useMemo(() => {
    const m = new Map<string, Room>()
    for (const r of rooms) m.set(r.code, r)
    return m
  }, [rooms])

  function fmtRoomCode(roomCode: string): string {
    const r = roomByCode.get(roomCode)
    return r?.is_special ? `🔒 ${roomCode}` : roomCode
  }

  React.useEffect(() => {
    if (!runId) return
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError('')
      setFetchWarnings([])
      setProgress({ done: 0, total: 0 })
      try {
        const [s, r, t] = await Promise.all([listTimeSlots(), listRooms(), listTeachers()])
        if (cancelled) return
        setSlots(s)
        setRooms(r.filter((x) => Boolean(x.is_active)))

        const active = t.filter((x) => Boolean(x.is_active)).sort((a, b) => a.code.localeCompare(b.code))
        setTeachers(active)
        setProgress({ done: 0, total: active.length })

        const results = await mapWithConcurrency(
          active,
          6,
          async (teacher) => {
            try {
              const data = await getFacultyTimetable(teacher.id, runId)
              return {
                teacherId: teacher.id,
                teacherCode: teacher.code,
                teacherName: teacher.full_name,
                entries: data,
              }
            } catch (ex: any) {
              const msg = String(ex?.message ?? ex)
              if (!cancelled) {
                setFetchWarnings((prev) => [...prev, `${teacher.code}: ${msg}`])
              }
              // Keep this teacher printable even if its fetch fails.
              return {
                teacherId: teacher.id,
                teacherCode: teacher.code,
                teacherName: teacher.full_name,
                entries: [],
              }
            }
          },
          (done, total) => {
            if (!cancelled) setProgress({ done, total })
          },
        )

        if (!cancelled) setGrids(results)
      } catch (ex: any) {
        if (!cancelled) setError(String(ex?.message ?? ex))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [runId])

  React.useEffect(() => {
    if (autoPrinted) return
    if (!runId) return
    if (loading) return
    if (error) return
    if (teachers.length === 0) return
    if (grids.length !== teachers.length) return

    setAutoPrinted(true)
    window.setTimeout(() => window.print(), 250)
  }, [autoPrinted, error, grids.length, loading, runId, teachers.length])

  return (
    <div className="min-h-dvh bg-white text-slate-900">
      <div className="no-print border-b bg-white px-6 py-4">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">Print All Faculty</div>
            <div className="mt-1 text-xs text-slate-500">Opens one print job with a page per teacher.</div>
          </div>
          <div className="flex items-center gap-2">
            <Link
              to={runId ? `/timetable?runId=${encodeURIComponent(runId)}` : '/timetable'}
              className="rounded-lg border px-3 py-2 text-sm text-slate-800 hover:bg-slate-50"
            >
              Back
            </Link>
            <button
              onClick={() => window.print()}
              className="rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white"
              disabled={!runId || loading || Boolean(error)}
              type="button"
            >
              Print / Save PDF
            </button>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-[1400px] px-6 py-6">
        <div className="mb-4">
          <div className="text-lg font-semibold">{programCode} · All Faculty</div>
          <div className="mt-1 text-xs text-slate-500">Run: {runId || '—'}</div>
          {loading ? (
            <div className="mt-2 text-xs text-slate-600">
              Loading… {progress.total ? `${progress.done}/${progress.total}` : ''}
            </div>
          ) : null}
        </div>

        {!runId ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">
            Missing runId. Open from the Timetable page.
          </div>
        ) : error ? (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>
        ) : fetchWarnings.length > 0 ? (
          <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            <div className="font-semibold">Some faculty timetables could not be loaded.</div>
            <div className="mt-1">Loaded {grids.length - fetchWarnings.length}/{grids.length} faculty for printing.</div>
          </div>
        ) : teachers.length === 0 && !loading ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">No teachers found.</div>
        ) : (
          <div className="space-y-10">
            {grids.map((g, idx) => (
              <div key={g.teacherId}>
                <PrintGrid
                  slots={slots}
                  title={`Faculty ${g.teacherCode} — ${g.teacherName}`}
                  subtitle={`Weekly load: ${g.entries.length} slots`}
                  items={g.entries}
                  fmtRoomCode={fmtRoomCode}
                />
                {idx < grids.length - 1 ? <div className="page-break mt-10" /> : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
