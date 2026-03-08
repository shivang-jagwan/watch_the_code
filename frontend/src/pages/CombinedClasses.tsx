import React from 'react'
import { Toast } from '../components/Toast'
import { useLayoutContext } from '../components/Layout'
import { PremiumSelect } from '../components/PremiumSelect'
import {
  createCombinedSubjectGroup,
  deleteCombinedSubjectGroup,
  listCombinedSubjectGroups,
  updateCombinedSubjectGroup,
  type CombinedSubjectGroupOut,
} from '../api/admin'
import { listSections, type Section } from '../api/sections'
import { listRunEntries, listRuns, type RunSummary, type TimetableEntry } from '../api/solver'
import { listSubjects, type Subject } from '../api/subjects'
import { listTeachers, type Teacher } from '../api/teachers'

type CombinedGroup = {
  id: string
  entries: TimetableEntry[]
}

export function CombinedClasses() {
  const { programCode, academicYearNumber } = useLayoutContext()
  const [tab, setTab] = React.useState<'rules' | 'analyze'>('rules')
  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)

  // Strict combined subject group rules UI
  const [year, setYear] = React.useState<number>(academicYearNumber)
  const [subjects, setSubjects] = React.useState<Subject[]>([])
  const [sections, setSections] = React.useState<Section[]>([])
  const [teachers, setTeachers] = React.useState<Teacher[]>([])
  const [groups, setGroups] = React.useState<CombinedSubjectGroupOut[]>([])
  const [subjectCode, setSubjectCode] = React.useState<string>('')

  const [newTeacherCode, setNewTeacherCode] = React.useState<string>('')
  const [newSelectedSectionCodes, setNewSelectedSectionCodes] = React.useState<Set<string>>(new Set())

  const [draftByGroupId, setDraftByGroupId] = React.useState<
    Record<string, { teacher_code: string; section_codes: Set<string> }>
  >({})

  const [runs, setRuns] = React.useState<RunSummary[]>([])
  const [runId, setRunId] = React.useState<string>('')
  // Analyzer (existing) UI
  const [analyzedGroups, setAnalyzedGroups] = React.useState<CombinedGroup[]>([])

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  const theorySubjects = React.useMemo(
    () =>
      subjects
        .filter((s) => String(s.subject_type).toUpperCase() === 'THEORY' && s.is_active)
        .sort((a, b) => a.code.localeCompare(b.code)),
    [subjects],
  )

  const activeSections = React.useMemo(
    () => sections.filter((s) => s.is_active).sort((a, b) => a.code.localeCompare(b.code)),
    [sections],
  )

  const activeTeachers = React.useMemo(
    () => teachers.filter((t) => t.is_active).sort((a, b) => a.code.localeCompare(b.code)),
    [teachers],
  )

  const groupsForSubject = React.useMemo(() => {
    if (!subjectCode) return []
    return groups.filter((g) => String(g.subject_code).toUpperCase() === String(subjectCode).toUpperCase())
  }, [groups, subjectCode])

  const usedSectionToGroupId = React.useMemo(() => {
    const map = new Map<string, string>()
    for (const g of groupsForSubject) {
      // Use draft state if the user has edited this group's sections,
      // so that sections removed from a group's draft become available
      // for a new group immediately (without needing to save first).
      const draft = draftByGroupId[g.id]
      const codes = draft
        ? Array.from(draft.section_codes)
        : (g.sections ?? []).map((s) => s.section_code)
      for (const code of codes) {
        map.set(String(code).toUpperCase(), g.id)
      }
    }
    return map
  }, [groupsForSubject, draftByGroupId])

  async function refreshRulesData(nextYear = year) {
    setLoading(true)
    try {
      const pc = programCode.trim()
      if (!pc) {
        setSubjects([])
        setSections([])
        setGroups([])
        setSubjectCode('')
        setNewSelectedSectionCodes(new Set())
        return
      }
      // Load core data first so the UI can still function
      // even if combined groups fail (e.g., missing DB migration).
      const [subjs, secs] = await Promise.all([
        listSubjects({ program_code: pc, academic_year_number: nextYear }),
        listSections({ program_code: pc, academic_year_number: nextYear }),
      ])
      setSubjects(subjs)
      setSections(secs)

      try {
        const gs = await listCombinedSubjectGroups({ program_code: pc, academic_year_number: nextYear })
        setGroups(gs)
      } catch (e: any) {
        setGroups([])
        showToast(`Load combined groups failed: ${String(e?.message ?? e)}`, 4000)
      }

      try {
        // Teachers are global in this app; load once per refresh.
        const ts = await listTeachers()
        setTeachers(ts)
      } catch (e: any) {
        setTeachers([])
        showToast(`Load teachers failed: ${String(e?.message ?? e)}`, 4000)
      }

      // If current subject isn't valid in this year anymore, reset.
      if (subjectCode) {
        const exists = subjs.some((s) => String(s.code).toUpperCase() === String(subjectCode).toUpperCase())
        if (!exists) setSubjectCode('')
      }
    } catch (e: any) {
      showToast(`Load combined rules failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  function toggleNewSection(code: string) {
    setNewSelectedSectionCodes((prev) => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  function toggleDraftSection(groupId: string, code: string) {
    setDraftByGroupId((prev) => {
      const cur = prev[groupId]
      if (!cur) return prev
      const nextSet = new Set(cur.section_codes)
      if (nextSet.has(code)) nextSet.delete(code)
      else nextSet.add(code)
      return { ...prev, [groupId]: { ...cur, section_codes: nextSet } }
    })
  }

  async function createGroup() {
    const pc = programCode.trim()
    if (!pc) {
      showToast('Select a program first', 3000)
      return
    }
    if (!subjectCode) {
      showToast('Select a THEORY subject first')
      return
    }
    if (!newTeacherCode) {
      showToast('Select a teacher')
      return
    }
    const section_codes = Array.from(newSelectedSectionCodes)
    if (section_codes.length < 2) {
      showToast('Select at least 2 sections')
      return
    }
    setLoading(true)
    try {
      await createCombinedSubjectGroup({
        program_code: pc,
        academic_year_number: year,
        subject_code: subjectCode,
        teacher_code: newTeacherCode,
        section_codes,
      })
      showToast('Combined group created')
      setNewSelectedSectionCodes(new Set())
      await refreshRulesData(year)
    } catch (e: any) {
      showToast(`Create failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function saveGroupEdits(groupId: string) {
    const draft = draftByGroupId[groupId]
    if (!draft) return
    if (!draft.teacher_code) {
      showToast('Select a teacher')
      return
    }
    const section_codes = Array.from(draft.section_codes)
    if (section_codes.length < 2) {
      showToast('Select at least 2 sections')
      return
    }
    setLoading(true)
    try {
      await updateCombinedSubjectGroup(groupId, {
        teacher_code: draft.teacher_code,
        section_codes,
      })
      showToast('Combined group updated')
      await refreshRulesData(year)
    } catch (e: any) {
      showToast(`Update failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function removeGroup(groupId: string) {
    setLoading(true)
    try {
      await deleteCombinedSubjectGroup(groupId)
      showToast('Combined group deleted (future solves only)')
      await refreshRulesData(year)
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function refreshRuns() {
    setLoading(true)
    try {
      const pc = programCode.trim()
      if (!pc) {
        setRuns([])
        setRunId('')
        return
      }
      const data = await listRuns({ program_code: pc, limit: 25 })
      setRuns(data)
      if (!runId && data.length > 0) setRunId(data[0].id)
    } catch (e: any) {
      showToast(`Load runs failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  function runTag(r: any): string {
    const scope = String(r?.parameters?.scope ?? '')
    if (scope === 'PROGRAM_GLOBAL') return 'GLOBAL'
    const year = r?.parameters?.academic_year_number
    if (year != null) return `YEAR ${year}`
    return 'LEGACY'
  }

  async function analyze() {
    if (!runId) {
      showToast('Select a run first')
      return
    }
    setLoading(true)
    try {
      const entries = await listRunEntries(runId)
      const map = new Map<string, TimetableEntry[]>()
      for (const e of entries) {
        if (!e.combined_class_id) continue
        const key = String(e.combined_class_id)
        const arr = map.get(key) ?? []
        arr.push(e)
        map.set(key, arr)
      }
      const out = Array.from(map.entries())
        .map(([id, es]) => ({ id, entries: es }))
        .sort((a, b) => b.entries.length - a.entries.length)
      setAnalyzedGroups(out)
      if (out.length === 0) showToast('No combined classes found in this run')
    } catch (e: any) {
      showToast(`Analyze failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  React.useEffect(() => {
    refreshRulesData(academicYearNumber)
    refreshRuns()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programCode, academicYearNumber])

  React.useEffect(() => {
    // keep local year in sync with global selection by default
    setYear(academicYearNumber)
    setNewSelectedSectionCodes(new Set())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [academicYearNumber])

  React.useEffect(() => {
    // Initialize drafts for the selected subject.
    const next: Record<string, { teacher_code: string; section_codes: Set<string> }> = {}
    for (const g of groupsForSubject) {
      next[g.id] = {
        teacher_code: String(g.teacher_code ?? ''),
        section_codes: new Set((g.sections ?? []).map((s) => s.section_code)),
      }
    }
    setDraftByGroupId(next)
    setNewTeacherCode('')
    setNewSelectedSectionCodes(new Set())
  }, [subjectCode, groupsForSubject])

  return (
    <div className="space-y-6">
      <Toast message={toast} />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-slate-900">Combined Classes</div>
          <div className="mt-1 text-sm text-slate-600">Configure strict combined-subject rules, or inspect runs.</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className={`rounded-2xl px-4 py-2 text-sm font-semibold disabled:opacity-50 ${
              tab === 'rules' ? 'bg-slate-900 text-white' : 'border bg-white text-slate-800'
            }`}
            onClick={() => setTab('rules')}
            disabled={loading}
          >
            Rules
          </button>
          <button
            className={`rounded-2xl px-4 py-2 text-sm font-semibold disabled:opacity-50 ${
              tab === 'analyze' ? 'bg-slate-900 text-white' : 'border bg-white text-slate-800'
            }`}
            onClick={() => setTab('analyze')}
            disabled={loading}
          >
            Analyze
          </button>
        </div>
      </div>

      {tab === 'rules' ? (
        <>
          <div className="rounded-3xl border bg-white p-5">
            <div className="grid gap-3 md:grid-cols-[220px_1fr_auto]">
              <div>
                <div className="text-xs font-semibold text-slate-600">Academic Year</div>
                <PremiumSelect
                  ariaLabel="Academic year"
                  className="mt-1 text-sm"
                  value={String(year)}
                  onValueChange={async (v) => {
                    const nextYear = Number(v)
                    setYear(nextYear)
                    setSubjectCode('')
                    setNewSelectedSectionCodes(new Set())
                    await refreshRulesData(nextYear)
                  }}
                  options={[1, 2, 3].map((n) => ({ value: String(n), label: `Year ${n}` }))}
                />
              </div>

              <div>
                <div className="text-xs font-semibold text-slate-600">THEORY Subject</div>
                <PremiumSelect
                  ariaLabel="Theory subject"
                  className="mt-1"
                  value={subjectCode || '__none__'}
                  onValueChange={(v) => setSubjectCode(v === '__none__' ? '' : v)}
                  options={[
                    { value: '__none__', label: 'Select a subject…' },
                    ...theorySubjects.map((s) => ({ value: s.code, label: `${s.code} — ${s.name}` })),
                  ]}
                />
              </div>

              <div className="flex items-end justify-end gap-2">
                <button
                  className="btn-secondary disabled:opacity-50"
                  onClick={() => refreshRulesData(year)}
                  disabled={loading}
                >
                  {loading ? 'Refreshing…' : 'Refresh'}
                </button>
              </div>
            </div>

            <div className="mt-4 text-xs text-slate-500">
              Each combined group schedules the subject together for its selected sections (one shared slot/teacher/LT room).
            </div>
          </div>

          <div className="rounded-3xl border bg-white p-5">
            <div className="text-sm font-semibold text-slate-900">Groups</div>
            <div className="mt-1 text-xs text-slate-500">Pick a subject to manage its groups.</div>

            {!subjectCode ? (
              <div className="mt-4 rounded-2xl border bg-slate-50 p-4 text-sm text-slate-700">
                Select a THEORY subject to view and edit its combined groups.
              </div>
            ) : (
              <div className="mt-4 space-y-4">
                {/* Existing groups */}
                {groupsForSubject.length === 0 ? (
                  <div className="rounded-2xl border bg-slate-50 p-4 text-sm text-slate-700">
                    No combined groups for this subject.
                  </div>
                ) : (
                  groupsForSubject.map((g, idx) => {
                    const draft = draftByGroupId[g.id]
                    const mySections = new Set((g.sections ?? []).map((s) => String(s.section_code).toUpperCase()))
                    return (
                      <div key={g.id} className="rounded-2xl border bg-white p-4">
                        <div className="flex flex-wrap items-end justify-between gap-3">
                          <div>
                            <div className="text-sm font-semibold text-slate-900">
                              Group {idx + 1}{' '}
                              <span className="font-normal text-slate-500">({g.id})</span>
                            </div>
                            <div className="mt-1 text-xs text-slate-500">
                              {g.subject_code} — {g.subject_name}
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              className="btn-secondary disabled:opacity-50"
                              onClick={() => saveGroupEdits(g.id)}
                              disabled={loading || !draft}
                            >
                              Save
                            </button>
                            <button
                              className="btn-danger disabled:opacity-50"
                              onClick={() => removeGroup(g.id)}
                              disabled={loading}
                            >
                              Delete
                            </button>
                          </div>
                        </div>

                        <div className="mt-4 grid gap-3 md:grid-cols-2">
                          <div>
                            <div className="text-xs font-semibold text-slate-600">Teacher</div>
                            <PremiumSelect
                              ariaLabel="Group teacher"
                              className="mt-1"
                              value={draft?.teacher_code || '__none__'}
                              onValueChange={(v) => {
                                setDraftByGroupId((prev) => ({
                                  ...prev,
                                  [g.id]: {
                                    teacher_code: v === '__none__' ? '' : v,
                                    section_codes: prev[g.id]?.section_codes ?? new Set(),
                                  },
                                }))
                              }}
                              options={[
                                { value: '__none__', label: 'Select a teacher…' },
                                ...activeTeachers.map((t) => ({ value: t.code, label: `${t.code} — ${t.full_name}` })),
                              ]}
                            />
                          </div>
                        </div>

                        <div className="mt-4">
                          <div className="text-xs font-semibold text-slate-600">Sections (select 2+)</div>
                          <div className="mt-2 grid gap-2 md:grid-cols-3">
                            {activeSections.length === 0 ? (
                              <div className="rounded-2xl border bg-slate-50 p-4 text-sm text-slate-700">
                                No sections found.
                              </div>
                            ) : (
                              activeSections.map((sec) => {
                                const secKey = String(sec.code).toUpperCase()
                                const usedBy = usedSectionToGroupId.get(secKey)
                                const disabled = Boolean(usedBy && usedBy !== g.id)
                                const checked = Boolean(draft?.section_codes?.has(sec.code))
                                return (
                                  <label key={sec.id} className="checkbox-row">
                                    <input
                                      type="checkbox"
                                      checked={checked}
                                      onChange={() => toggleDraftSection(g.id, sec.code)}
                                      disabled={disabled || loading}
                                    />
                                    <span className="font-semibold text-slate-900">{sec.code}</span>
                                    <span className="text-slate-600">
                                      {sec.name}
                                      {disabled ? ' (used in another group)' : ''}
                                    </span>
                                  </label>
                                )
                              })
                            )}
                          </div>
                          {mySections.size === 0 ? (
                            <div className="mt-2 text-xs text-slate-500">This group currently has no sections.</div>
                          ) : null}
                        </div>
                      </div>
                    )
                  })
                )}

                {/* Create new group */}
                <div className="rounded-2xl border bg-slate-50 p-4">
                  <div className="flex flex-wrap items-end justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-slate-900">Add Group</div>
                      <div className="mt-1 text-xs text-slate-500">
                        Sections cannot be repeated across groups for the same subject.
                      </div>
                    </div>
                    <button
                      className="btn-primary disabled:opacity-50"
                      onClick={createGroup}
                      disabled={loading || !subjectCode}
                    >
                      Create
                    </button>
                  </div>

                  <div className="mt-4 grid gap-3 md:grid-cols-2">
                    <div>
                      <div className="text-xs font-semibold text-slate-600">Teacher</div>
                      <PremiumSelect
                        ariaLabel="New group teacher"
                        className="mt-1"
                        value={newTeacherCode || '__none__'}
                        onValueChange={(v) => setNewTeacherCode(v === '__none__' ? '' : v)}
                        options={[
                          { value: '__none__', label: 'Select a teacher…' },
                          ...activeTeachers.map((t) => ({ value: t.code, label: `${t.code} — ${t.full_name}` })),
                        ]}
                      />
                    </div>
                  </div>

                  <div className="mt-4">
                    <div className="text-xs font-semibold text-slate-600">Sections (select 2+)</div>
                    <div className="mt-2 grid gap-2 md:grid-cols-3">
                      {activeSections.length === 0 ? (
                        <div className="rounded-2xl border bg-white p-4 text-sm text-slate-700">No sections found.</div>
                      ) : (
                        activeSections.map((sec) => {
                          const secKey = String(sec.code).toUpperCase()
                          const disabled = usedSectionToGroupId.has(secKey)
                          const checked = newSelectedSectionCodes.has(sec.code)
                          return (
                            <label key={sec.id} className="checkbox-row">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleNewSection(sec.code)}
                                disabled={disabled || loading}
                              />
                              <span className="font-semibold text-slate-900">{sec.code}</span>
                              <span className="text-slate-600">
                                {sec.name}
                                {disabled ? ' (used in another group)' : ''}
                              </span>
                            </label>
                          )
                        })
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="rounded-3xl border bg-white p-5">
            <div className="grid gap-3 md:grid-cols-[1fr_auto]">
              <PremiumSelect
                ariaLabel="Solver run"
                className="w-full text-sm"
                searchable
                searchPlaceholder="Search runs…"
                value={runs.length === 0 ? '__none__' : runId}
                onValueChange={(v) => {
                  if (v === '__none__') return
                  setRunId(v)
                }}
                options={
                  runs.length === 0
                    ? [{ value: '__none__', label: 'No runs found', disabled: true }]
                    : runs.map((r) => ({
                        value: r.id,
                        label: `[${runTag(r)}] ${r.status} — ${new Date(r.created_at).toLocaleString()} (${r.id})`,
                      }))
                }
              />
              <button
                className="rounded-2xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                onClick={analyze}
                disabled={loading || !runId}
              >
                Analyze
              </button>
            </div>
          </div>

          <div className="rounded-3xl border bg-white p-5">
            <div className="text-sm font-semibold text-slate-900">Groups</div>
            <div className="mt-1 text-xs text-slate-500">Only entries with a non-null combined_class_id are shown.</div>

            <div className="mt-4 space-y-3">
              {analyzedGroups.length === 0 ? (
                <div className="rounded-2xl border bg-slate-50 p-4 text-sm text-slate-700">No groups to display.</div>
              ) : (
                analyzedGroups.map((g) => (
                  <div key={g.id} className="rounded-2xl border bg-white p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-slate-900">{g.id}</div>
                      <div className="text-xs text-slate-500">{g.entries.length} entries</div>
                    </div>
                    <div className="mt-2 overflow-x-auto">
                      <table className="min-w-full text-left text-sm">
                        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                          <tr>
                            <th className="px-3 py-2">Section</th>
                            <th className="px-3 py-2">Subject</th>
                            <th className="px-3 py-2">Teacher</th>
                            <th className="px-3 py-2">Room</th>
                            <th className="px-3 py-2">Slot</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-200">
                          {g.entries.slice(0, 50).map((e) => (
                            <tr key={e.id} className="hover:bg-slate-50">
                              <td className="px-3 py-2 font-medium text-slate-900">{e.section_code}</td>
                              <td className="px-3 py-2 text-slate-700">{e.subject_code}</td>
                              <td className="px-3 py-2 text-slate-700">{e.teacher_code}</td>
                              <td className="px-3 py-2 text-slate-700">{e.room_code}</td>
                              <td className="px-3 py-2 text-slate-700">D{e.day_of_week} #{e.slot_index}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {g.entries.length > 50 ? (
                      <div className="mt-2 text-xs text-slate-500">Showing first 50 entries for this group.</div>
                    ) : null}
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
