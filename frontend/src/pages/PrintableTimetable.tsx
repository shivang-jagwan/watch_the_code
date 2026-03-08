import React from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useLayoutContext } from '../components/Layout'
import { listRunEntries, listTimeSlots, type TimeSlot } from '../api/solver'
import {
  OfficialTimetablePrint,
  OFFICIAL_PRINT_STYLES,
  adaptTimetableEntry,
} from '../components/OfficialTimetablePrint'

export function PrintableTimetable() {
  const { programCode } = useLayoutContext()
  const [params] = useSearchParams()

  const runId = params.get('runId') ?? ''
  const sectionCode = params.get('section') ?? ''
  const semester = params.get('semester') ?? ''
  const effectiveDate = params.get('effectiveDate') ?? ''

  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState('')
  const [entries, setEntries] = React.useState<Awaited<ReturnType<typeof listRunEntries>>>([])
  const [slots, setSlots] = React.useState<TimeSlot[]>([])

  React.useEffect(() => {
    if (!runId || !sectionCode) return
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError('')
      try {
        const [e, s] = await Promise.all([listRunEntries(runId, sectionCode), listTimeSlots()])
        if (!cancelled) { setEntries(e); setSlots(s) }
      } catch (ex: any) {
        if (!cancelled) setError(String(ex?.message ?? ex))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [runId, sectionCode])

  const officialEntries = React.useMemo(() => entries.map(adaptTimetableEntry), [entries])
  const sectionTitle = entries[0]?.section_name ?? sectionCode
  const canRender = Boolean(runId) && Boolean(sectionCode) && slots.length > 0 && officialEntries.length > 0
  const backHref = `/timetable/print?runId=${encodeURIComponent(runId)}&section=${encodeURIComponent(sectionCode)}`

  return (
    <div className="min-h-dvh bg-white text-slate-900">
      <style>{OFFICIAL_PRINT_STYLES}</style>

      {/* toolbar */}
      <div className="no-print border-b bg-white px-6 py-3 shadow-sm">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">Official Print Timetable</div>
            <div className="mt-0.5 text-xs text-slate-500">A4 landscape  Times New Roman  subject legend</div>
          </div>
          <div className="flex items-center gap-2">
            <Link to={backHref} className="rounded-lg border px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"> Back</Link>
            <button
              onClick={() => window.print()}
              disabled={!canRender || loading}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              Print Timetable
            </button>
          </div>
        </div>
      </div>

      {/* printable body */}
      <div className="mx-auto max-w-[1400px] px-6 py-6">
        {!runId || !sectionCode ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">Missing parameters. Navigate here from the Timetable page.</div>
        ) : loading ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">Loading timetable</div>
        ) : error ? (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>
        ) : !canRender ? (
          <div className="rounded-xl border bg-slate-50 p-4 text-sm text-slate-700">No timetable entries found for this section and run.</div>
        ) : (
          <OfficialTimetablePrint
            type="section"
            title={sectionTitle}
            programCode={programCode}
            semester={semester}
            effectiveDate={effectiveDate}
            entries={officialEntries}
            slots={slots}
          />
        )}
      </div>
    </div>
  )
}
