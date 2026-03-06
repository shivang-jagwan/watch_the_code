import React from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useLayoutContext } from '../components/Layout'
import { listRunEntries, listTimeSlots, type TimeSlot } from '../api/solver'
import { getSectionTimetable, type TimetableGridEntry } from '../api/timetable'

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
}: {
  slots: TimeSlot[]
  title: string
  subtitle: string
  items: TimetableGridEntry[]
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
                                  {g.items.map((e, idx) => (
                                    <div key={`${e.section_code}:${e.subject_code}:${idx}`}>
                                      <span className="font-semibold">{e.section_code}</span>
                                      <span className="text-slate-500"> · </span>
                                      <span>{e.subject_code}</span>
                                      <span className="text-slate-500"> · </span>
                                      <span>{e.teacher_name}</span>
                                      <span className="text-slate-500"> · </span>
                                      <span>{e.room_code}</span>
                                      <span className="text-slate-500"> · </span>
                                      <span>Y{e.year_number}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ))}

                            {grouped.nonElective.map((e, idx) => (
                              <div key={`${e.section_code}:${e.subject_code}:${idx}`} className="rounded-xl border bg-emerald-50 p-2">
                                <div className="flex items-center justify-between gap-2">
                                  <div className="text-xs font-semibold text-slate-900">{e.subject_code}</div>
                                  <div className="inline-flex rounded-full bg-emerald-600 px-2 py-0.5 text-[11px] font-semibold text-white">
                                    Y{e.year_number}
                                  </div>
                                </div>
                                <div className="mt-0.5 text-[11px] text-slate-700">
                                  <span className="font-semibold">{e.section_code}</span>
                                  <span className="text-slate-500"> · </span>
                                  <span>{e.teacher_name}</span>
                                  <span className="text-slate-500"> · </span>
                                  <span>{e.room_code}</span>
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

export function TimetablePrintAllSections() {
  const { programCode } = useLayoutContext()
  const [params] = useSearchParams()
  const runId = params.get('runId') ?? ''

  const [loading, setLoading] = React.useState(false)
  const [progress, setProgress] = React.useState<{ done: number; total: number }>({ done: 0, total: 0 })
  const [error, setError] = React.useState<string>('')
  const [fetchWarnings, setFetchWarnings] = React.useState<string[]>([])

  const [slots, setSlots] = React.useState<TimeSlot[]>([])
  const [sections, setSections] = React.useState<Array<{ id: string; code: string }>>([])
  const [grids, setGrids] = React.useState<
    Array<{ sectionId: string; sectionCode: string; entries: TimetableGridEntry[] }>
  >([])
  const [autoPrinted, setAutoPrinted] = React.useState(false)

  React.useEffect(() => {
    if (!runId) return
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError('')
      setFetchWarnings([])
      setProgress({ done: 0, total: 0 })
      try {
        const [s, runEntries] = await Promise.all([listTimeSlots(), listRunEntries(runId)])
        if (cancelled) return
        setSlots(s)

        const map = new Map<string, string>()
        for (const e of runEntries) {
          if (e.section_id && e.section_code) map.set(e.section_id, e.section_code)
        }
        const list = Array.from(map.entries())
          .map(([id, code]) => ({ id, code }))
          .sort((a, b) => a.code.localeCompare(b.code))

        setSections(list)
        setProgress({ done: 0, total: list.length })

        const results = await mapWithConcurrency(
          list,
          6,
          async (sec) => {
            try {
              const data = await getSectionTimetable(sec.id, runId)
              return { sectionId: sec.id, sectionCode: sec.code, entries: data }
            } catch (ex: any) {
              const msg = String(ex?.message ?? ex)
              if (!cancelled) {
                setFetchWarnings((prev) => [...prev, `${sec.code}: ${msg}`])
              }
              // Keep this section printable even if its fetch fails.
              return { sectionId: sec.id, sectionCode: sec.code, entries: [] }
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
    if (sections.length === 0) return
    if (grids.length !== sections.length) return

    setAutoPrinted(true)
    window.setTimeout(() => window.print(), 250)
  }, [autoPrinted, error, grids.length, loading, runId, sections.length])

  return (
    <div className="min-h-dvh bg-white text-slate-900">
      <div className="no-print border-b bg-white px-6 py-4">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">Print All Sections</div>
            <div className="mt-1 text-xs text-slate-500">
              Opens one print job with a page per section.
            </div>
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
          <div className="text-lg font-semibold">{programCode} · All Sections</div>
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
            <div className="font-semibold">Some sections could not be loaded.</div>
            <div className="mt-1">Loaded {grids.length - fetchWarnings.length}/{grids.length} sections for printing.</div>
          </div>
        ) : sections.length === 0 && !loading ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">
            No sections found in this run.
          </div>
        ) : (
          <div className="space-y-10">
            {grids.map((g, idx) => (
              <div key={g.sectionId}>
                <PrintGrid
                  slots={slots}
                  title={`Section ${g.sectionCode}`}
                  subtitle={`Weekly load: ${g.entries.length} slots`}
                  items={g.entries}
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
