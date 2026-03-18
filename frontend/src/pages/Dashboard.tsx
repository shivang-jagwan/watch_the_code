import React from 'react'
import { Link } from 'react-router-dom'
import { SummaryCard } from '../components/SummaryCard'
import { useLayoutContext } from '../components/Layout'
import { listSections } from '../api/sections'
import { listSubjects } from '../api/subjects'
import { listTeachers } from '../api/teachers'
import { listRooms } from '../api/rooms'
import { listElectiveBlocks } from '../api/admin'
import { clearTimetables } from '../api/admin'
import { getRun, listRuns, listTimeSlots, type RunDetail, type RunSummary } from '../api/solver'

export function Dashboard() {
  const { programCode, academicYearNumber } = useLayoutContext()

  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState<string>('')
  const [actionMessage, setActionMessage] = React.useState<string>('')
  const [clearingAll, setClearingAll] = React.useState(false)
  const [reloadKey, setReloadKey] = React.useState(0)

  const [sectionsTotal, setSectionsTotal] = React.useState<number>(0)
  const [sectionsActive, setSectionsActive] = React.useState<number>(0)
  const [subjectsTotal, setSubjectsTotal] = React.useState<number>(0)
  const [subjectsActive, setSubjectsActive] = React.useState<number>(0)
  const [teachersTotal, setTeachersTotal] = React.useState<number>(0)
  const [teachersActive, setTeachersActive] = React.useState<number>(0)
  const [roomsTotal, setRoomsTotal] = React.useState<number>(0)
  const [roomsActive, setRoomsActive] = React.useState<number>(0)
  const [slotTotal, setSlotTotal] = React.useState<number>(0)
  const [slotDays, setSlotDays] = React.useState<number>(0)
  const [blocksTotal, setBlocksTotal] = React.useState<number>(0)
  const [blocksActive, setBlocksActive] = React.useState<number>(0)

  const [latestRun, setLatestRun] = React.useState<RunSummary | null>(null)
  const [latestRunDetail, setLatestRunDetail] = React.useState<RunDetail | null>(null)

  function runTag(r: RunSummary): string {
    const scope = String(r.parameters?.scope ?? '')
    if (scope === 'PROGRAM_GLOBAL') return 'GLOBAL'
    const year = r.parameters?.academic_year_number
    if (year != null) return `YEAR ${year}`
    return 'LEGACY'
  }

  function fmtShortId(id: string) {
    return (id || '').split('-')[0] ?? id
  }

  async function onClearAllData() {
    if (!programCode.trim()) {
      setActionMessage('Select a program first')
      return
    }

    const ok = window.confirm(
      `This will clear generated timetable data for Program ${programCode.trim()} in Year ${academicYearNumber}. Continue?`,
    )
    if (!ok) return
    const confirmWord = window.prompt(`Type DELETE to confirm clear for Year ${academicYearNumber}`)
    if (confirmWord !== 'DELETE') {
      setActionMessage('Clear cancelled')
      return
    }

    setClearingAll(true)
    try {
      const result = await clearTimetables({
        confirm: 'DELETE',
        program_code: programCode.trim(),
        academic_year_number: academicYearNumber,
      })
      if (result.ok) {
        setActionMessage(`Cleared ${result.deleted ?? 0} records for Program ${programCode.trim()} Year ${academicYearNumber}`)
        setReloadKey((k) => k + 1)
      } else {
        setActionMessage(result.message || 'Clear failed')
      }
    } catch (e: any) {
      setActionMessage(`Clear failed: ${String(e?.message ?? e)}`)
    } finally {
      setClearingAll(false)
    }
  }

  function HealthBadge({ ok }: { ok: boolean }) {
    return (
      <span
        className={
          [
            'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
            ok ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200' : 'bg-amber-50 text-amber-800 ring-1 ring-amber-200',
          ].join(' ')
        }
      >
        {ok ? 'OK' : 'Needs setup'}
      </span>
    )
  }

  React.useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError('')
      try {
        const pc = programCode.trim()

        const results = pc
          ? await Promise.allSettled([
              listSections({ program_code: pc, academic_year_number: academicYearNumber }),
              listSubjects({ program_code: pc, academic_year_number: academicYearNumber }),
              listTeachers(),
              listRooms(),
              listTimeSlots(),
              listElectiveBlocks({ program_code: pc, academic_year_number: academicYearNumber }),
              listRuns({ program_code: pc, limit: 15 }),
            ])
          : await Promise.allSettled([
              Promise.resolve([]),
              Promise.resolve([]),
              listTeachers(),
              listRooms(),
              listTimeSlots(),
              Promise.resolve([]),
              Promise.resolve([]),
            ])

        if (cancelled) return

        const [sectionsRes, subjectsRes, teachersRes, roomsRes, slotsRes, blocksRes, runsRes] = results

        if (sectionsRes.status === 'fulfilled') {
          setSectionsTotal(sectionsRes.value.length)
          setSectionsActive(sectionsRes.value.filter((s) => Boolean(s.is_active)).length)
        }
        if (subjectsRes.status === 'fulfilled') {
          setSubjectsTotal(subjectsRes.value.length)
          setSubjectsActive(subjectsRes.value.filter((s) => Boolean(s.is_active)).length)
        }
        if (teachersRes.status === 'fulfilled') {
          setTeachersTotal(teachersRes.value.length)
          setTeachersActive(teachersRes.value.filter((t) => Boolean(t.is_active)).length)
        }
        if (roomsRes.status === 'fulfilled') {
          setRoomsTotal(roomsRes.value.length)
          setRoomsActive(roomsRes.value.filter((r) => Boolean(r.is_active)).length)
        }
        if (slotsRes.status === 'fulfilled') {
          const slots = slotsRes.value
          setSlotTotal(slots.length)
          setSlotDays(new Set(slots.map((s) => s.day_of_week)).size)
        }
        if (blocksRes.status === 'fulfilled') {
          setBlocksTotal(blocksRes.value.length)
          setBlocksActive(blocksRes.value.filter((b) => Boolean(b.is_active)).length)
        }

        if (runsRes.status === 'fulfilled') {
          const sorted = runsRes.value
            .slice()
            .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
          const r = sorted[0] ?? null
          setLatestRun(r)
        }

        if (!pc) {
          setSectionsTotal(0)
          setSectionsActive(0)
          setSubjectsTotal(0)
          setSubjectsActive(0)
          setBlocksTotal(0)
          setBlocksActive(0)
          setLatestRun(null)
        }
      } catch (e: any) {
        if (!cancelled) setError(String(e?.message ?? e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [programCode, academicYearNumber, reloadKey])

  React.useEffect(() => {
    if (!latestRun) {
      setLatestRunDetail(null)
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const d = await getRun(latestRun.id)
        if (!cancelled) setLatestRunDetail(d)
      } catch {
        if (!cancelled) setLatestRunDetail(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [latestRun?.id])

  return (
    <div className="space-y-6">
      <div>
        <div className="text-lg font-semibold text-slate-900">Dashboard</div>
        <div className="mt-1 text-sm text-slate-600">
          Program {programCode.trim() ? programCode : '—'} · Year {academicYearNumber}
        </div>
      </div>

      {error ? (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>
      ) : null}
      {actionMessage ? (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">{actionMessage}</div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title="Sections"
          value={loading ? '…' : sectionsTotal}
          subtitle={loading ? 'Loading' : `${sectionsActive} active`}
        />
        <SummaryCard
          title="Subjects"
          value={loading ? '…' : subjectsTotal}
          subtitle={loading ? 'Loading' : `${subjectsActive} active`}
        />
        <SummaryCard
          title="Teachers"
          value={loading ? '…' : teachersTotal}
          subtitle={loading ? 'Loading' : `${teachersActive} active`}
        />
        <SummaryCard
          title="Rooms"
          value={loading ? '…' : roomsTotal}
          subtitle={loading ? 'Loading' : `${roomsActive} active`}
        />
        <SummaryCard
          title="Time Slots"
          value={loading ? '…' : slotTotal}
          subtitle={loading ? 'Loading' : `${slotDays} days configured`}
        />
        <SummaryCard
          title="Elective Blocks"
          value={loading ? '…' : blocksTotal}
          subtitle={loading ? 'Loading' : `${blocksActive} active`}
        />
        <SummaryCard
          title="Latest Run"
          value={latestRun ? fmtShortId(latestRun.id) : '—'}
          subtitle={latestRun ? `${runTag(latestRun)} · ${latestRun.status}` : 'No runs found'}
        />
        <SummaryCard
          title="Latest Run Details"
          value={latestRunDetail ? `${latestRunDetail.entries_total} entries` : '—'}
          subtitle={latestRunDetail ? `${latestRunDetail.conflicts_total} conflicts` : 'Open Conflicts to review'}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-2xl border bg-white p-4 shadow-sm">
          <div className="text-sm font-semibold text-slate-900">Quick Actions</div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link className="btn-primary text-sm" to="/generate">Generate</Link>
            <Link className="btn-secondary text-sm" to="/timetable">View Timetable</Link>
            <Link className="btn-secondary text-sm" to="/conflicts">Conflicts</Link>
            <Link className="btn-secondary text-sm" to="/manual-editor">Manual Editor</Link>
            <button
              type="button"
              className="btn-danger text-sm font-semibold disabled:opacity-50"
              onClick={onClearAllData}
              disabled={clearingAll}
            >
              {clearingAll ? 'Clearing…' : 'Clear All Data'}
            </button>
          </div>
        </div>

        <div className="rounded-2xl border bg-white p-4 shadow-sm">
          <div className="text-sm font-semibold text-slate-900">Master Data</div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link className="btn-secondary text-sm" to="/teachers">Teachers</Link>
            <Link className="btn-secondary text-sm" to="/subjects">Subjects</Link>
            <Link className="btn-secondary text-sm" to="/sections">Sections</Link>
            <Link className="btn-secondary text-sm" to="/rooms">Rooms</Link>
            <Link className="btn-secondary text-sm" to="/time-slots">Time Slots</Link>
          </div>
        </div>

        <div className="rounded-2xl border bg-white p-4 shadow-sm">
          <div className="text-sm font-semibold text-slate-900">Elective Blocks</div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link className="btn-secondary text-sm" to="/elective-blocks">Elective Blocks</Link>
            <Link className="btn-secondary text-sm" to="/combined-classes">Combined Classes</Link>
          </div>
        </div>
      </div>

      <div className="rounded-2xl border bg-white p-4 text-sm text-slate-700 shadow-sm">
        <div className="font-semibold text-slate-900">Data health</div>
        <div className="mt-2 space-y-1">
          <div>
            <HealthBadge ok={slotTotal > 0} /> <span className="ml-2">Time slots configured: {slotTotal}</span>
          </div>
          <div>
            <HealthBadge ok={roomsActive > 0} /> <span className="ml-2">Active rooms: {roomsActive}</span>
          </div>
          <div>
            <HealthBadge ok={teachersActive > 0} /> <span className="ml-2">Active teachers: {teachersActive}</span>
          </div>
          <div>
            <HealthBadge ok={sectionsActive > 0} /> <span className="ml-2">Active sections (selected year): {sectionsActive}</span>
          </div>
          <div>
            <HealthBadge ok={subjectsActive > 0} /> <span className="ml-2">Active subjects (selected year): {subjectsActive}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
