import React from 'react'
import { useSearchParams } from 'react-router-dom'
import { listRunEntries, listTimeSlots, type TimeSlot, type TimetableEntry } from '../api/solver'
import {
  OfficialTimetablePrint,
  OFFICIAL_PRINT_STYLES,
  adaptTimetableEntry,
  type OfficialEntry,
} from '../components/OfficialTimetablePrint'

type RoomBlock = { roomCode: string; title: string; entries: OfficialEntry[] }

export function OfficialPrintAllRooms() {
  const [params] = useSearchParams()
  const runId = params.get('runId') ?? ''
  const semester = params.get('semester') ?? ''
  const effectiveDate = params.get('effectiveDate') ?? ''

  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState('')
  const [blocks, setBlocks] = React.useState<RoomBlock[]>([])
  const [slots, setSlots] = React.useState<TimeSlot[]>([])

  React.useEffect(() => {
    if (!runId) return
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError('')
      try {
        const [rawEntries, s] = await Promise.all([listRunEntries(runId), listTimeSlots()])
        if (cancelled) return

        // Group by room_code, preserve first-appearance order
        const map = new Map<string, { title: string; entries: OfficialEntry[] }>()
        for (const e of rawEntries as TimetableEntry[]) {
          const key = e.room_code ?? 'UNKNOWN'
          if (!map.has(key)) map.set(key, { title: key, entries: [] })
          map.get(key)!.entries.push(adaptTimetableEntry(e))
        }
        const built: RoomBlock[] = []
        map.forEach(({ title, entries }, roomCode) => built.push({ roomCode, title, entries }))
        built.sort((a, b) => a.roomCode.localeCompare(b.roomCode))

        setBlocks(built)
        setSlots(s)
      } catch (ex: any) {
        if (!cancelled) setError(String(ex?.message ?? ex))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [runId])

  // Auto-print once data is ready
  React.useEffect(() => {
    if (!loading && blocks.length > 0 && params.get('auto') === '1') {
      window.print()
    }
  }, [loading, blocks, params])

  return (
    <div className="min-h-dvh bg-white text-slate-900">
      <style>{OFFICIAL_PRINT_STYLES}</style>

      {/* toolbar */}
      <div className="no-print border-b bg-white px-6 py-3 shadow-sm">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">Official Timetable — All Rooms</div>
            <div className="mt-0.5 text-xs text-slate-500">
              {loading ? 'Loading…' : blocks.length > 0 ? `${blocks.length} room(s) · A4 landscape` : 'No data'}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => window.print()}
              disabled={loading || blocks.length === 0}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              Print All
            </button>
          </div>
        </div>
      </div>

      {/* printable body */}
      <div className="mx-auto max-w-[1400px] px-6 py-6">
        {!runId ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">Missing runId parameter.</div>
        ) : loading ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">Loading all room timetables…</div>
        ) : error ? (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>
        ) : blocks.length === 0 ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">No entries found for this run.</div>
        ) : (
          blocks.map((block, idx) => (
            <OfficialTimetablePrint
              key={block.roomCode}
              type="room"
              title={block.title}
              semester={semester}
              effectiveDate={effectiveDate}
              entries={block.entries}
              slots={slots}
              pageBreak={idx < blocks.length - 1}
            />
          ))
        )}
      </div>
    </div>
  )
}
