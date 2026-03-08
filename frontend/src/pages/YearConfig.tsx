import React from 'react'
import { Link } from 'react-router-dom'
import { useLayoutContext } from '../components/Layout'
import { Toast } from '../components/Toast'
import {
  listSections,
  getSectionTimeWindows,
  type Section,
  type SectionTimeWindow,
} from '../api/sections'
import {
  listCombinedSubjectGroups,
  deleteCombinedSubjectGroup,
  listTeacherSubjectSections,
  listElectiveBlocks,
  deleteElectiveBlock,
  type CombinedSubjectGroupOut,
  type TeacherSubjectSectionAssignmentRow,
  type ElectiveBlockOut,
} from '../api/admin'
import {
  listSpecialAllotments,
  deleteSpecialAllotment,
  listFixedEntries,
  deleteFixedEntry,
  type SpecialAllotment,
  type FixedTimetableEntry,
} from '../api/solver'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function dayName(d: number) {
  return DAYS[d] ?? `D${d}`
}

// ── Section accordion ──────────────────────────────────────────────────────────

function Section({
  title,
  count,
  linkTo,
  linkLabel,
  children,
}: {
  title: string
  count?: number
  linkTo?: string
  linkLabel?: string
  children: React.ReactNode
}) {
  const [open, setOpen] = React.useState(true)
  return (
    <div className="rounded-xl border border-green-200 bg-white shadow-sm overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-5 py-4 text-left hover:bg-green-50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className="font-semibold text-slate-800">{title}</span>
          {count !== undefined && (
            <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
              {count}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {linkTo && (
            <Link
              to={linkTo}
              onClick={(e) => e.stopPropagation()}
              className="rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700 transition-colors"
            >
              {linkLabel ?? 'Manage'}
            </Link>
          )}
          <svg
            viewBox="0 0 24 24"
            className={`size-4 text-slate-400 transition-transform duration-200 ${open ? 'rotate-0' : '-rotate-90'}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        </div>
      </button>
      {open && <div className="border-t border-green-100">{children}</div>}
    </div>
  )
}

// ── Generic table ──────────────────────────────────────────────────────────────

function Table({
  headers,
  children,
  empty,
}: {
  headers: string[]
  children: React.ReactNode
  empty?: string
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-slate-50">
            {headers.map((h) => (
              <th
                key={h}
                className="border-b border-slate-200 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
      {!children ||
        (React.Children.count(children) === 0 && (
          <p className="px-4 py-6 text-center text-sm text-slate-400">
            {empty ?? 'No records'}
          </p>
        ))}
    </div>
  )
}

function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <td className={`border-b border-slate-100 px-4 py-2.5 text-slate-700 ${className ?? ''}`}>
      {children}
    </td>
  )
}

function DeleteBtn({ onClick, loading }: { onClick: () => void; loading?: boolean }) {
  return (
    <button
      type="button"
      disabled={loading}
      onClick={onClick}
      className="rounded px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50 transition-colors"
    >
      {loading ? '…' : 'Delete'}
    </button>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────

export function YearConfig() {
  const { programCode, academicYearNumber } = useLayoutContext()

  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [deletingId, setDeletingId] = React.useState<string | null>(null)

  const [sections, setSections] = React.useState<Section[]>([])

  // Data buckets
  const [combinedGroups, setCombinedGroups] = React.useState<CombinedSubjectGroupOut[]>([])
  const [teacherMappings, setTeacherMappings] = React.useState<TeacherSubjectSectionAssignmentRow[]>([])
  const [electiveBlocks, setElectiveBlocks] = React.useState<ElectiveBlockOut[]>([])
  const [specialAllotments, setSpecialAllotments] = React.useState<SpecialAllotment[]>([])
  const [fixedEntries, setFixedEntries] = React.useState<FixedTimetableEntry[]>([])
  const [windowsBySectionId, setWindowsBySectionId] = React.useState<
    Record<string, SectionTimeWindow[]>
  >({})

  function showToast(msg: string, ms = 3000) {
    setToast(msg)
    window.setTimeout(() => setToast(''), ms)
  }

  async function refreshAll() {
    if (!programCode || !academicYearNumber) {
      showToast('Select a program and year first', 3000)
      return
    }
    setLoading(true)
    try {
      // 1. Sections (needed for per-section fetches)
      const secs = await listSections({
        program_code: programCode,
        academic_year_number: academicYearNumber,
      })
      const activeSecs = secs.filter((s) => s.is_active)
      setSections(activeSecs)

      // 2. Parallel: combined groups, teacher mappings, elective blocks
      const [cg, tm, eb] = await Promise.all([
        listCombinedSubjectGroups({ program_code: programCode, academic_year_number: academicYearNumber }),
        listTeacherSubjectSections(),
        listElectiveBlocks({ program_code: programCode, academic_year_number: academicYearNumber }),
      ])
      setCombinedGroups(cg)
      // Filter teacher mappings to sections in this year
      const sectionIds = new Set(activeSecs.map((s) => s.id))
      const filteredTm = tm.filter((row) =>
        row.sections.some((sec) => sectionIds.has(sec.section_id)),
      )
      setTeacherMappings(filteredTm)
      setElectiveBlocks(eb)

      // 3. Per-section: special allotments, fixed entries, time windows
      if (activeSecs.length > 0) {
        const results = await Promise.allSettled(
          activeSecs.flatMap((sec) => [
            listSpecialAllotments({ section_id: sec.id }),
            listFixedEntries({ section_id: sec.id }),
            getSectionTimeWindows(sec.id),
          ]),
        )

        const allSA: SpecialAllotment[] = []
        const allFE: FixedTimetableEntry[] = []
        const winMap: Record<string, SectionTimeWindow[]> = {}

        for (let i = 0; i < activeSecs.length; i++) {
          const saResult = results[i * 3]
          const feResult = results[i * 3 + 1]
          const wResult = results[i * 3 + 2]

          if (saResult.status === 'fulfilled') {
            allSA.push(...(saResult.value as SpecialAllotment[]))
          }
          if (feResult.status === 'fulfilled') {
            allFE.push(...(feResult.value as FixedTimetableEntry[]))
          }
          if (wResult.status === 'fulfilled') {
            const wr = wResult.value as { section_id: string; windows: SectionTimeWindow[] }
            winMap[activeSecs[i].id] = wr.windows ?? []
          } else {
            winMap[activeSecs[i].id] = []
          }
        }

        setSpecialAllotments(allSA)
        setFixedEntries(allFE)
        setWindowsBySectionId(winMap)
      } else {
        setSpecialAllotments([])
        setFixedEntries([])
        setWindowsBySectionId({})
      }
    } catch (e: any) {
      showToast(`Load failed: ${String(e?.message ?? e)}`, 4000)
    } finally {
      setLoading(false)
    }
  }

  React.useEffect(() => {
    if (programCode && academicYearNumber) {
      refreshAll()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programCode, academicYearNumber])

  // ── Delete handlers ────────────────────────────────────────────────────────

  async function handleDeleteCombinedGroup(id: string) {
    setDeletingId(id)
    try {
      await deleteCombinedSubjectGroup(id)
      setCombinedGroups((prev) => prev.filter((g) => g.id !== id))
      showToast('Combined group deleted')
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 4000)
    } finally {
      setDeletingId(null)
    }
  }

  async function handleDeleteElectiveBlock(id: string) {
    setDeletingId(id)
    try {
      await deleteElectiveBlock(id)
      setElectiveBlocks((prev) => prev.filter((b) => b.id !== id))
      showToast('Elective block deleted')
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 4000)
    } finally {
      setDeletingId(null)
    }
  }

  async function handleDeleteSpecialAllotment(id: string) {
    setDeletingId(id)
    try {
      await deleteSpecialAllotment(id)
      setSpecialAllotments((prev) => prev.filter((e) => e.id !== id))
      showToast('Special allotment deleted')
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 4000)
    } finally {
      setDeletingId(null)
    }
  }

  async function handleDeleteFixedEntry(id: string) {
    setDeletingId(id)
    try {
      await deleteFixedEntry(id)
      setFixedEntries((prev) => prev.filter((e) => e.id !== id))
      showToast('Fixed entry deleted')
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 4000)
    } finally {
      setDeletingId(null)
    }
  }

  // ── Derived ────────────────────────────────────────────────────────────────

  // Sort sections consistently
  const sortedSections = React.useMemo(
    () => [...sections].sort((a, b) => a.code.localeCompare(b.code)),
    [sections],
  )

  // Teacher mappings filtered to this year's sections
  const sectionIdsInYear = React.useMemo(
    () => new Set(sections.map((s) => s.id)),
    [sections],
  )

  const filteredTeacherMappings = React.useMemo(
    () =>
      teacherMappings.map((row) => ({
        ...row,
        sections: row.sections.filter((s) => sectionIdsInYear.has(s.section_id)),
      })).filter((row) => row.sections.length > 0),
    [teacherMappings, sectionIdsInYear],
  )

  const sectionById = React.useMemo(() => {
    const m: Record<string, Section> = {}
    for (const s of sections) m[s.id] = s
    return m
  }, [sections])

  // ── Render ─────────────────────────────────────────────────────────────────

  const hasProgram = Boolean(programCode && academicYearNumber)

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <Toast message={toast} />

      {/* Header */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Year Configuration</h1>
          {hasProgram && (
            <p className="mt-1 text-sm text-slate-500">
              {programCode} · Year {academicYearNumber} · {sections.length} active sections
            </p>
          )}
        </div>
        <button
          type="button"
          disabled={loading || !hasProgram}
          onClick={refreshAll}
          className="flex items-center gap-2 rounded-xl bg-green-600 px-4 py-2.5 text-sm font-semibold text-white shadow hover:bg-green-700 disabled:opacity-50 transition-colors"
        >
          {loading ? (
            <>
              <span className="inline-block size-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
              Loading…
            </>
          ) : (
            <>
              <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 4v5h5M20 20v-5h-5" />
                <path d="M4 9a9 9 0 0 1 15.6-6.4M20 15a9 9 0 0 1-15.6 6.4" />
              </svg>
              Refresh
            </>
          )}
        </button>
      </div>

      {!hasProgram ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-8 text-center text-sm text-amber-700">
          Select a program and academic year from the top bar to view the configuration.
        </div>
      ) : (
        <div className="space-y-4">
          {/* ── 1. Combined Classes ── */}
          <Section
            title="Combined Classes"
            count={combinedGroups.length}
            linkTo="/combined-classes"
            linkLabel="Manage"
          >
            {combinedGroups.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-slate-400">No combined class groups defined</p>
            ) : (
              <Table headers={['Group ID', 'Subject', 'Teacher', 'Sections', '']}>
                {combinedGroups.map((g) => (
                  <tr key={g.id} className="hover:bg-slate-50">
                    <Td className="font-mono text-xs text-slate-400">{g.id.slice(0, 8)}…</Td>
                    <Td>
                      <span className="font-medium">{g.subject_code}</span>
                      <span className="ml-1.5 text-slate-400 text-xs">{g.subject_name}</span>
                    </Td>
                    <Td>{g.teacher_code ?? <span className="text-slate-400 italic">unassigned</span>}</Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {g.sections.map((s) => (
                          <span
                            key={s.section_id}
                            className="rounded bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800"
                          >
                            {s.section_code}
                          </span>
                        ))}
                      </div>
                    </Td>
                    <Td>
                      <DeleteBtn
                        loading={deletingId === g.id}
                        onClick={() => handleDeleteCombinedGroup(g.id)}
                      />
                    </Td>
                  </tr>
                ))}
              </Table>
            )}
          </Section>

          {/* ── 2. Special Allotments ── */}
          <Section
            title="Special Allotments"
            count={specialAllotments.length}
            linkTo="/special-allotments"
            linkLabel="Manage"
          >
            {specialAllotments.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-slate-400">No special allotments defined</p>
            ) : (
              <Table headers={['Section', 'Subject', 'Teacher', 'Room', 'Day', 'Slot', 'Reason', '']}>
                {specialAllotments.map((e) => (
                  <tr key={e.id} className="hover:bg-slate-50">
                    <Td>
                      <span className="rounded bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800">
                        {e.section_code}
                      </span>
                    </Td>
                    <Td>{e.subject_code}</Td>
                    <Td>{e.teacher_code}</Td>
                    <Td>{e.room_code}</Td>
                    <Td>{dayName(e.day_of_week)}</Td>
                    <Td>
                      #{e.slot_index} ({e.start_time}–{e.end_time})
                    </Td>
                    <Td className="text-slate-400 text-xs">{e.reason ?? '—'}</Td>
                    <Td>
                      <DeleteBtn
                        loading={deletingId === e.id}
                        onClick={() => handleDeleteSpecialAllotment(e.id)}
                      />
                    </Td>
                  </tr>
                ))}
              </Table>
            )}
          </Section>

          {/* ── 3. Teacher Subject Section Mapping ── */}
          <Section
            title="Teacher Subject Section Mapping"
            count={filteredTeacherMappings.length}
            linkTo="/combined-classes"
            linkLabel="Manage"
          >
            {filteredTeacherMappings.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-slate-400">No teacher-subject assignments for this year</p>
            ) : (
              <Table headers={['Teacher', 'Subject', 'Sections']}>
                {filteredTeacherMappings.map((row) => (
                  <tr key={`${row.teacher_id}-${row.subject_id}`} className="hover:bg-slate-50">
                    <Td>
                      <span className="font-medium">{row.teacher_code ?? row.teacher_id.slice(0, 8)}</span>
                      {row.teacher_name && (
                        <span className="ml-1.5 text-slate-400 text-xs">{row.teacher_name}</span>
                      )}
                    </Td>
                    <Td>
                      <span className="font-medium">{row.subject_code}</span>
                      <span className="ml-1.5 text-slate-400 text-xs">{row.subject_name}</span>
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {row.sections.map((s) => (
                          <span
                            key={s.section_id}
                            className="rounded bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-800"
                          >
                            {s.section_code}
                          </span>
                        ))}
                      </div>
                    </Td>
                  </tr>
                ))}
              </Table>
            )}
          </Section>

          {/* ── 4. Elective Blocks ── */}
          <Section
            title="Elective Blocks"
            count={electiveBlocks.length}
            linkTo="/elective-blocks"
            linkLabel="Manage"
          >
            {electiveBlocks.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-slate-400">No elective blocks defined</p>
            ) : (
              <Table headers={['Name / Code', 'Active', 'Subjects', 'Sections', '']}>
                {electiveBlocks.map((b) => (
                  <tr key={b.id} className="hover:bg-slate-50">
                    <Td>
                      <span className="font-medium">{b.name}</span>
                      {b.code && <span className="ml-1.5 text-slate-400 text-xs">[{b.code}]</span>}
                    </Td>
                    <Td>
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                          b.is_active ? 'bg-green-100 text-green-800' : 'bg-slate-100 text-slate-500'
                        }`}
                      >
                        {b.is_active ? 'Yes' : 'No'}
                      </span>
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {b.subjects.map((s) => (
                          <span
                            key={s.id}
                            className="rounded bg-purple-100 px-1.5 py-0.5 text-xs font-medium text-purple-800"
                            title={s.teacher_name ?? undefined}
                          >
                            {s.subject_code}
                          </span>
                        ))}
                      </div>
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {b.sections.map((s) => (
                          <span
                            key={s.section_id}
                            className="rounded bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800"
                          >
                            {s.section_code}
                          </span>
                        ))}
                      </div>
                    </Td>
                    <Td>
                      <DeleteBtn
                        loading={deletingId === b.id}
                        onClick={() => handleDeleteElectiveBlock(b.id)}
                      />
                    </Td>
                  </tr>
                ))}
              </Table>
            )}
          </Section>

          {/* ── 5. Fixed Timetable Entries ── */}
          <Section
            title="Fixed Timetable Entries"
            count={fixedEntries.length}
            linkTo="/manual-editor"
            linkLabel="Manage"
          >
            {fixedEntries.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-slate-400">No fixed timetable entries</p>
            ) : (
              <Table headers={['Section', 'Subject', 'Teacher', 'Room', 'Day', 'Slot', '']}>
                {fixedEntries.map((e) => (
                  <tr key={e.id} className="hover:bg-slate-50">
                    <Td>
                      <span className="rounded bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800">
                        {e.section_code}
                      </span>
                    </Td>
                    <Td>{e.subject_code}</Td>
                    <Td>{e.teacher_code}</Td>
                    <Td>{e.room_code}</Td>
                    <Td>{dayName(e.day_of_week)}</Td>
                    <Td>
                      #{e.slot_index} ({e.start_time}–{e.end_time})
                    </Td>
                    <Td>
                      <DeleteBtn
                        loading={deletingId === e.id}
                        onClick={() => handleDeleteFixedEntry(e.id)}
                      />
                    </Td>
                  </tr>
                ))}
              </Table>
            )}
          </Section>

          {/* ── 6. Section Windows & Breaks ── */}
          <Section
            title="Section Time Windows"
            count={sortedSections.length}
            linkTo="/sections"
            linkLabel="Manage"
          >
            {sortedSections.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-slate-400">No active sections</p>
            ) : (
              <div className="divide-y divide-slate-100">
                {sortedSections.map((sec) => {
                  const wins = windowsBySectionId[sec.id] ?? []
                  return (
                    <div key={sec.id} className="px-4 py-3">
                      <div className="mb-2 flex items-center gap-2">
                        <span className="rounded bg-green-100 px-2 py-0.5 text-xs font-semibold text-green-800">
                          {sec.code}
                        </span>
                        <span className="text-sm text-slate-600">{sec.name}</span>
                        <span className="text-xs text-slate-400">Track: {sec.track}</span>
                      </div>
                      {wins.length === 0 ? (
                        <p className="text-xs text-slate-400 italic">No time windows defined (uses all slots)</p>
                      ) : (
                        <div className="flex flex-wrap gap-1.5">
                          {wins
                            .sort((a, b) => a.day_of_week - b.day_of_week || a.start_slot_index - b.start_slot_index)
                            .map((w) => (
                              <span
                                key={w.id}
                                className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-700"
                              >
                                {dayName(w.day_of_week)} slots {w.start_slot_index}–{w.end_slot_index}
                              </span>
                            ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </Section>
        </div>
      )}
    </div>
  )
}
