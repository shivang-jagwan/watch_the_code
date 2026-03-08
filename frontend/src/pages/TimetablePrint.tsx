import React from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useLayoutContext } from '../components/Layout'
import { listRooms, type Room } from '../api/rooms'
import {
  listRunEntries,
  listTimeSlots,
  listFixedEntries,
  listSectionRequiredSubjects,
  type TimeSlot,
  type TimetableEntry,
  type FixedTimetableEntry,
  type RequiredSubject,
} from '../api/solver'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function cellKey(day: number, slotIndex: number) {
  return `${day}:${slotIndex}`
}

function groupForCell(entries: TimetableEntry[]) {
  const nonElective: TimetableEntry[] = []
  const electiveByBlock = new Map<string, { name: string; items: TimetableEntry[] }>()

  for (const e of entries) {
    const blockId = (e as any).elective_block_id as string | undefined
    if (!blockId) {
      nonElective.push(e)
      continue
    }
    const name = String((e as any).elective_block_name ?? 'Elective Block')
    const group = electiveByBlock.get(blockId) ?? { name, items: [] }
    group.items.push(e)
    electiveByBlock.set(blockId, group)
  }

  const electiveGroups = Array.from(electiveByBlock.entries())
    .sort((a, b) => a[1].name.localeCompare(b[1].name))
    .map(([blockId, g]) => ({ blockId, name: g.name, items: g.items }))

  nonElective.sort((a, b) => `${a.subject_code}-${a.teacher_code}`.localeCompare(`${b.subject_code}-${b.teacher_code}`))
  for (const g of electiveGroups) {
    g.items.sort((a, b) => `${a.subject_code}-${a.teacher_code}`.localeCompare(`${b.subject_code}-${b.teacher_code}`))
  }

  return { nonElective, electiveGroups }
}

export function TimetablePrint() {
  const { programCode, academicYearNumber } = useLayoutContext()
  const [params] = useSearchParams()
  const runId = params.get('runId') ?? ''
  const sectionCode = params.get('section') ?? ''

  const [loading, setLoading] = React.useState(false)
  const [slots, setSlots] = React.useState<TimeSlot[]>([])
  const [entries, setEntries] = React.useState<TimetableEntry[]>([])
  const [fixedEntries, setFixedEntries] = React.useState<FixedTimetableEntry[]>([])
  const [requiredSubjects, setRequiredSubjects] = React.useState<RequiredSubject[]>([])
  const [error, setError] = React.useState<string>('')
  const [rooms, setRooms] = React.useState<Room[]>([])

  const roomById = React.useMemo(() => {
    const m = new Map<string, Room>()
    for (const r of rooms) m.set(r.id, r)
    return m
  }, [rooms])

  function fmtRoom(roomId: string, roomCode: string): string {
    const r = roomById.get(roomId)
    return r?.is_special ? `🔒 ${roomCode}` : roomCode
  }

  React.useEffect(() => {
    if (!runId || !sectionCode) return
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError('')
      try {
        const [s, e, r] = await Promise.all([listTimeSlots(), listRunEntries(runId, sectionCode), listRooms()])
        if (cancelled) return
        setSlots(s)
        setEntries(e)
        setRooms(r.filter((x) => Boolean(x.is_active)))

        const sectionId = e[0]?.section_id
        if (sectionId) {
          const [fe, subj] = await Promise.all([
            listFixedEntries({ section_id: sectionId }),
            listSectionRequiredSubjects({ section_id: sectionId }),
          ])
          if (!cancelled) {
            setFixedEntries(fe)
            setRequiredSubjects(subj)
          }
        } else {
          setFixedEntries([])
          setRequiredSubjects([])
        }
      } catch (ex: any) {
        if (cancelled) return
        setError(String(ex?.message ?? ex))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [runId, sectionCode])

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

  const days = React.useMemo(() => Array.from(slotsByDay.keys()).sort((a, b) => a - b), [slotsByDay])

  const maxSlotIndex = React.useMemo(() => {
    let max = -1
    for (const s of slots) max = Math.max(max, s.slot_index)
    return max
  }, [slots])

  const byCell = React.useMemo(() => {
    const map = new Map<string, TimetableEntry[]>()
    for (const e of entries) {
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
  }, [entries])

  const labSpans = React.useMemo(() => {
    const spanByCell = new Map<string, { rowSpan: number; entry: TimetableEntry; endTime: string }>()
    const skipCells = new Set<string>()

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

        spanByCell.set(k1, { rowSpan: 2, entry: e1, endTime: e2.end_time })
        skipCells.add(k2)
      }
    }

    return { spanByCell, skipCells }
  }, [byCell, days, maxSlotIndex])

  const fixedByCell = React.useMemo(() => {
    const subjById = new Map(requiredSubjects.map((s) => [s.id, s]))
    const map = new Map<string, { entry: FixedTimetableEntry; isStart: boolean }>()
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

  const canRender = runId && sectionCode && days.length > 0 && maxSlotIndex >= 0

  return (
    <div className="min-h-dvh bg-white text-slate-900">
      <style>{`
        @media print {
          .no-print { display: none !important; }
          body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
        }
      `}</style>

      <div className="no-print border-b bg-white px-6 py-4">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">Print Timetable</div>
            <div className="mt-1 text-xs text-slate-500">Use your browser’s “Save as PDF”.</div>
          </div>
          <div className="flex items-center gap-2">
            <Link
              to={`/timetable?runId=${encodeURIComponent(runId)}&section=${encodeURIComponent(sectionCode)}`}
              className="rounded-lg border px-3 py-2 text-sm text-slate-800 hover:bg-slate-50"
            >
              Back
            </Link>
            <Link
              to={`/timetable/print-official?runId=${encodeURIComponent(runId)}&section=${encodeURIComponent(sectionCode)}`}
              className="rounded-lg border border-indigo-300 bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-800 hover:bg-indigo-100"
            >
              Official Format
            </Link>
            <button
              onClick={() => window.print()}
              className="rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white"
              disabled={!canRender || loading}
            >
              Print / Save PDF
            </button>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-[1400px] px-6 py-6">
        <div className="mb-4">
          <div className="text-lg font-semibold">{programCode} · Year {academicYearNumber} · Section {sectionCode}</div>
          <div className="mt-1 text-xs text-slate-500">Run: {runId}</div>
        </div>

        {!runId || !sectionCode ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">
            Missing parameters. Open from the Timetable page.
          </div>
        ) : loading ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">Loading…</div>
        ) : error ? (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>
        ) : !canRender ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">
            No time slots or entries found.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr>
                  <th className="border px-2 py-2 text-left">Slot</th>
                  {days.map((d) => (
                    <th key={d} className="border px-2 py-2 text-left">{WEEKDAYS[d] ?? `Day ${d}`}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: maxSlotIndex + 1 }).map((_, slotIndex) => {
                  const labelSlot = slots.find((s) => s.slot_index === slotIndex) ?? null
                  return (
                    <tr key={slotIndex}>
                      <td className="border px-2 py-2 align-top">
                        <div className="font-semibold">#{slotIndex}</div>
                        <div className="text-[10px] text-slate-600">
                          {labelSlot ? `${labelSlot.start_time}-${labelSlot.end_time}` : ''}
                        </div>
                      </td>
                      {days.map((d) => {
                        const key = cellKey(d, slotIndex)
                        if (labSpans.skipCells.has(key)) return null

                        const labSpan = labSpans.spanByCell.get(key) ?? null
                        const items = byCell.get(key) ?? []
                        const grouped = groupForCell(items)
                        const fixedInfo = fixedByCell.get(key) ?? null
                        return (
                          <td
                            key={`${d}:${slotIndex}`}
                            className={
                              'border px-2 py-2 align-top ' +
                              (fixedInfo ? 'bg-amber-50' : '')
                            }
                            rowSpan={labSpan?.rowSpan}
                          >
                            {items.length === 0 ? (
                              fixedInfo ? (
                                <div>
                                  <div className="font-semibold">🔒 {fixedInfo.entry.subject_code}</div>
                                  <div className="text-[10px] text-slate-600">
                                    {fixedInfo.entry.teacher_code} · {fmtRoom(fixedInfo.entry.room_id, fixedInfo.entry.room_code)}
                                  </div>
                                  {!fixedInfo.isStart ? (
                                    <div className="text-[10px] text-slate-500">(lab block continuation)</div>
                                  ) : null}
                                </div>
                              ) : (
                                <div className="text-slate-400">Free</div>
                              )
                            ) : labSpan ? (
                              <div>
                                <div className="font-semibold">{fixedInfo ? '🔒 ' : ''}{labSpan.entry.subject_code} (Lab · 2 hrs)</div>
                                <div className="text-[10px] text-slate-600">{labSpan.entry.teacher_code} · {fmtRoom(labSpan.entry.room_id, labSpan.entry.room_code)}</div>
                                <div className="text-[10px] text-slate-500">{WEEKDAYS[labSpan.entry.day_of_week] ?? `Day ${labSpan.entry.day_of_week}`} #{labSpan.entry.slot_index} ({labSpan.entry.start_time}-{labSpan.endTime})</div>
                              </div>
                            ) : (
                              <div className="space-y-1">
                                {grouped.electiveGroups.map((g) => (
                                  <div key={g.blockId} className="rounded border bg-indigo-50 p-1">
                                    <div className="font-semibold">{fixedInfo ? '🔒 ' : ''}ELECTIVE: {g.name}</div>
                                    <div className="mt-0.5 space-y-0.5 text-[10px] text-slate-700">
                                      {g.items.map((e) => (
                                        <div key={e.id}>
                                          {e.subject_code} · {e.teacher_code} · {fmtRoom(e.room_id, e.room_code)}
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                ))}

                                {grouped.nonElective.map((e) => (
                                  <div key={e.id}>
                                    <div className="font-semibold">{fixedInfo ? '🔒 ' : ''}{e.subject_code}</div>
                                    <div className="text-[10px] text-slate-600">{e.teacher_code} · {fmtRoom(e.room_id, e.room_code)}</div>
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
        )}
      </div>
    </div>
  )
}
