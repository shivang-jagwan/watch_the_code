import React from 'react'
import { Toast } from '../components/Toast'
import { createTeacher, deleteTeacher, listTeachers, Teacher, updateTeacher, TeacherPut } from '../api/teachers'
import { TeacherEditModal } from '../components/TeacherEditModal'
import { useLayoutContext } from '../components/Layout'
import { listSubjects, Subject } from '../api/subjects'
import { PremiumSelect } from '../components/PremiumSelect'
import {
  listTeacherSubjectSections,
  setTeacherSubjectSections,
  TeacherSubjectSectionAssignmentRow,
} from '../api/admin'
import { listSections, Section } from '../api/sections'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

type FormState = {
  code: string
  full_name: string
  weekly_off_day: string
  max_per_day: number
  max_per_week: number
  max_continuous: number
  is_active: boolean
}

const DEFAULT_FORM: FormState = {
  code: '',
  full_name: '',
  weekly_off_day: '',
  max_per_day: 4,
  max_per_week: 20,
  max_continuous: 3,
  is_active: true,
}

export function Teachers() {
  const { programCode, academicYearNumber } = useLayoutContext()
  const [toast, setToast] = React.useState('')
  const [items, setItems] = React.useState<Teacher[]>([])
  const [loading, setLoading] = React.useState(false)
  const [query, setQuery] = React.useState('')
  const [form, setForm] = React.useState<FormState>(DEFAULT_FORM)

  const [tab, setTab] = React.useState<'manage' | 'assignments'>('manage')

  // Strict teacher-subject-section assignments
  const [assignTeacherId, setAssignTeacherId] = React.useState<string>('')
  const [assignSubjectId, setAssignSubjectId] = React.useState<string>('')
  const [assignSubjects, setAssignSubjects] = React.useState<Subject[]>([])
  const [assignSections, setAssignSections] = React.useState<Section[]>([])
  const [assignSelectedSections, setAssignSelectedSections] = React.useState<Set<string>>(new Set())
  const [assignRows, setAssignRows] = React.useState<TeacherSubjectSectionAssignmentRow[]>([])
  const [assignLoading, setAssignLoading] = React.useState(false)
  const [assignSaving, setAssignSaving] = React.useState(false)

  const [editOpen, setEditOpen] = React.useState(false)
  const [editTeacher, setEditTeacher] = React.useState<Teacher | null>(null)
  const [editSaving, setEditSaving] = React.useState(false)

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  function validateCaps(
    maxPerDay: number,
    maxPerWeek: number,
    maxContinuous: number,
  ): string[] {
    const errors: string[] = []
    if (maxPerDay > 6) errors.push('Max per day cannot exceed 6')
    if (maxPerWeek > 30) errors.push('Max per week cannot exceed 30')
    if (maxPerDay > maxPerWeek) errors.push('Max per day must be <= Max per week')
    if (maxContinuous > maxPerDay) errors.push('Max continuous must be <= Max per day')
    if (maxPerDay * 6 < maxPerWeek) errors.push('Max per week is too high for Max per day across 6 days')
    return errors
  }

  function openEdit(t: Teacher) {
    setEditTeacher(t)
    setEditOpen(true)
  }

  function closeEdit() {
    if (editSaving) return
    setEditOpen(false)
    setEditTeacher(null)
  }

  function loadIndicator(t: Teacher): {
    label: string
    className: string
    title: string
  } {
    const days = 6 - (t.weekly_off_day == null ? 0 : 1)
    const capacity = Math.max(0, days) * Math.max(0, Number(t.max_per_day ?? 0))
    const ratio = capacity > 0 ? Number(t.max_per_week ?? 0) / capacity : Infinity
    const pct = Number.isFinite(ratio) ? Math.round(ratio * 100) : null

    let className = 'bg-slate-100 text-slate-700'
    if (ratio <= 0.6) className = 'bg-emerald-50 text-emerald-700'
    else if (ratio < 0.8) className = 'bg-amber-50 text-amber-800'
    else className = 'bg-rose-50 text-rose-700'

    return {
      label: pct == null ? '—' : `${pct}%`,
      className,
      title: `Load ratio = max_per_week / ((6 - leave) × max_per_day) = ${
        Number.isFinite(ratio) ? ratio.toFixed(2) : '∞'
      }`,
    }
  }

  async function refresh() {
    setLoading(true)
    try {
      const data = await listTeachers()
      setItems(data)
    } catch (e: any) {
      showToast(`Load failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  React.useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  React.useEffect(() => {
    // reset assignment picker when year changes
    setAssignSubjectId('')
    setAssignSelectedSections(new Set())
  }, [academicYearNumber])

  async function refreshAssignmentData() {
    setAssignLoading(true)
    try {
      const pc = programCode.trim()
      if (!pc) {
        setAssignSubjects([])
        setAssignSections([])
        return
      }
      const [subjects, sections] = await Promise.all([
        listSubjects({ program_code: pc, academic_year_number: academicYearNumber }),
        listSections({ program_code: pc, academic_year_number: academicYearNumber }),
      ])
      setAssignSubjects(subjects)
      setAssignSections(sections)
    } catch (e: any) {
      showToast(`Assignment data load failed: ${String(e?.message ?? e)}`, 3500)
      setAssignSubjects([])
      setAssignSections([])
    } finally {
      setAssignLoading(false)
    }
  }

  async function refreshTeacherAssignments(teacherId: string) {
    if (!teacherId) {
      setAssignRows([])
      return
    }
    setAssignLoading(true)
    try {
      const rows = await listTeacherSubjectSections({ teacher_id: teacherId })
      setAssignRows(rows)
    } catch (e: any) {
      showToast(`Assignments load failed: ${String(e?.message ?? e)}`, 3500)
      setAssignRows([])
    } finally {
      setAssignLoading(false)
    }
  }

  async function refreshTeacherSubjectSelection(teacherId: string, subjectId: string) {
    if (!teacherId || !subjectId) {
      setAssignSelectedSections(new Set())
      return
    }
    setAssignLoading(true)
    try {
      const rows = await listTeacherSubjectSections({ teacher_id: teacherId, subject_id: subjectId })
      const row = rows[0]
      setAssignSelectedSections(new Set((row?.sections ?? []).map((s) => s.section_id)))
    } catch (e: any) {
      showToast(`Assignment load failed: ${String(e?.message ?? e)}`, 3500)
      setAssignSelectedSections(new Set())
    } finally {
      setAssignLoading(false)
    }
  }

  React.useEffect(() => {
    if (tab !== 'assignments') return
    refreshAssignmentData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, programCode, academicYearNumber])

  React.useEffect(() => {
    if (tab !== 'assignments') return
    refreshTeacherAssignments(assignTeacherId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, assignTeacherId])

  React.useEffect(() => {
    if (tab !== 'assignments') return
    refreshTeacherSubjectSelection(assignTeacherId, assignSubjectId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, assignTeacherId, assignSubjectId])

  async function onCreate() {
    const capErrors = validateCaps(form.max_per_day, form.max_per_week, form.max_continuous)
    if (capErrors.length) {
      showToast(capErrors[0], 3500)
      return
    }
    setLoading(true)
    try {
      await createTeacher({
        code: form.code.trim(),
        full_name: form.full_name.trim(),
        weekly_off_day: form.weekly_off_day === '' ? null : Number(form.weekly_off_day),
        max_per_day: Number(form.max_per_day),
        max_per_week: Number(form.max_per_week),
        max_continuous: Number(form.max_continuous),
        is_active: Boolean(form.is_active),
      })
      showToast('Teacher saved')
      setForm((f) => ({ ...f, code: '', full_name: '' }))
      await refresh()
    } catch (e: any) {
      showToast(`Save failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function onSaveEdit(payload: TeacherPut) {
    if (!editTeacher) return
    const capErrors = validateCaps(payload.max_per_day, payload.max_per_week, payload.max_continuous)
    if (capErrors.length) {
      showToast(capErrors[0], 3500)
      return
    }

    setEditSaving(true)
    try {
      await updateTeacher(editTeacher.id, payload)
      showToast('Teacher updated')
      closeEdit()
      await refresh()
    } catch (e: any) {
      showToast(`Update failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setEditSaving(false)
    }
  }

  async function onDelete(id: string) {
    if (!confirm('Delete this teacher and all dependent data? This cannot be undone.')) return
    setLoading(true)
    try {
      await deleteTeacher(id, true)
      showToast('Teacher deleted')
      await refresh()
    } catch (e: any) {
      const msg = String(e?.message ?? e)
      showToast(`Delete failed: ${msg}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter((t) => {
      return (
        t.code.toLowerCase().includes(q) ||
        t.full_name.toLowerCase().includes(q)
      )
    })
  }, [items, query])

  return (
    <div className="space-y-6">
      <Toast message={toast} />

      <TeacherEditModal
        open={editOpen}
        teacher={editTeacher}
        loading={editSaving}
        onClose={closeEdit}
        onSave={onSaveEdit}
      />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-slate-900">Teachers</div>
          <div className="mt-1 text-sm text-slate-600">
            Create and manage faculty constraints used by the solver.
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="mr-2 inline-flex rounded-2xl border bg-white p-1">
            <button
              className={
                'rounded-xl px-3 py-2 text-sm font-medium ' +
                (tab === 'manage' ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-50')
              }
              onClick={() => setTab('manage')}
              type="button"
            >
              Manage
            </button>
            <button
              className={
                'rounded-xl px-3 py-2 text-sm font-medium ' +
                (tab === 'assignments' ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-50')
              }
              onClick={() => setTab('assignments')}
              type="button"
            >
              Assignments
            </button>
          </div>
          <button
            className="rounded-2xl border bg-white px-4 py-2 text-sm font-medium text-slate-800 disabled:opacity-50"
            onClick={refresh}
            disabled={loading}
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {tab === 'assignments' ? (
        <section className="rounded-3xl border bg-white p-5">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-sm font-semibold text-slate-900">Assignments</div>
              <div className="mt-1 text-xs text-slate-500">
                Assign a teacher to a subject for specific sections (strict binding).
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                className="rounded-2xl border bg-white px-4 py-2 text-sm font-medium text-slate-800 disabled:opacity-50"
                onClick={() => {
                  refreshAssignmentData()
                  refreshTeacherAssignments(assignTeacherId)
                }}
                disabled={assignLoading || assignSaving}
              >
                {assignLoading ? 'Loading…' : 'Refresh'}
              </button>
            </div>
          </div>

          <div className="mt-5 grid gap-4 md:grid-cols-3">
            <div>
              <label className="text-xs font-medium text-slate-600" htmlFor="assign_teacher">
                Teacher
              </label>
              <PremiumSelect
                id="assign_teacher"
                ariaLabel="Teacher"
                className="mt-1"
                value={assignTeacherId || '__none__'}
                onValueChange={(v) => {
                  const next = v === '__none__' ? '' : v
                  setAssignTeacherId(next)
                  setAssignSubjectId('')
                  setAssignSelectedSections(new Set())
                }}
                options={[
                  { value: '__none__', label: 'Select…' },
                  ...items
                    .slice()
                    .sort((a, b) => (a.full_name || '').localeCompare(b.full_name || ''))
                    .map((t) => ({ value: t.id, label: `${t.full_name} (${t.code})` })),
                ]}
              />
              <div className="mt-1 text-[11px] text-slate-500">Program: {programCode}, Year: {academicYearNumber}</div>
            </div>

            <div className="md:col-span-2">
              <label className="text-xs font-medium text-slate-600" htmlFor="assign_subject">
                Subject
              </label>
              <PremiumSelect
                id="assign_subject"
                ariaLabel="Subject"
                className="mt-1"
                disabled={!assignTeacherId}
                value={assignSubjectId || '__none__'}
                onValueChange={(v) => setAssignSubjectId(v === '__none__' ? '' : v)}
                options={[
                  { value: '__none__', label: 'Select…' },
                  ...assignSubjects
                    .slice()
                    .sort((a, b) => (a.code || '').localeCompare(b.code || ''))
                    .map((s) => ({ value: s.id, label: `${s.code} — ${s.name}` })),
                ]}
              />
            </div>
          </div>

          <div className="mt-5 rounded-2xl border bg-slate-50 p-4">
            <div className="text-xs font-semibold text-slate-800">Sections</div>
            <div className="mt-2 text-xs text-slate-600">
              Tick the sections this teacher will teach for the selected subject.
            </div>

            {!assignTeacherId || !assignSubjectId ? (
              <div className="mt-4 text-sm text-slate-600">Select teacher and subject to edit assignments.</div>
            ) : assignSections.length === 0 ? (
              <div className="mt-4 text-sm text-slate-600">
                {assignLoading ? 'Loading sections…' : 'No sections found.'}
              </div>
            ) : (
              <div className="mt-4 grid gap-2 md:grid-cols-2">
                {assignSections
                  .slice()
                  .sort((a, b) => (a.code || '').localeCompare(b.code || ''))
                  .map((sec) => {
                    const checked = assignSelectedSections.has(sec.id)
                    return (
                      <label key={sec.id} className="checkbox-row rounded-lg">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => {
                            const next = new Set(assignSelectedSections)
                            if (e.target.checked) next.add(sec.id)
                            else next.delete(sec.id)
                            setAssignSelectedSections(next)
                          }}
                        />
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-slate-900">
                            {sec.code} <span className="text-xs font-normal text-slate-500">({sec.track})</span>
                          </div>
                          <div className="truncate text-xs text-slate-600">{sec.name}</div>
                        </div>
                      </label>
                    )
                  })}
              </div>
            )}
          </div>

          <div className="mt-5 flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs text-slate-500">
              Note: only one teacher can be active per (section, subject).
            </div>
            <div className="flex items-center gap-2">
              <button
                className="btn-secondary disabled:opacity-50"
                disabled={!assignTeacherId || !assignSubjectId || assignSaving || assignLoading}
                onClick={async () => {
                  if (!assignTeacherId || !assignSubjectId) return
                  setAssignSaving(true)
                  try {
                    await setTeacherSubjectSections({
                      teacher_id: assignTeacherId,
                      subject_id: assignSubjectId,
                      section_ids: [],
                    })
                    showToast('Cleared assignment')
                    await refreshTeacherAssignments(assignTeacherId)
                    setAssignSelectedSections(new Set())
                  } catch (e: any) {
                    showToast(`Clear failed: ${String(e?.message ?? e)}`, 3500)
                  } finally {
                    setAssignSaving(false)
                  }
                }}
                type="button"
              >
                Clear
              </button>
              <button
                className="btn-primary disabled:opacity-50"
                disabled={!assignTeacherId || !assignSubjectId || assignSaving || assignLoading}
                onClick={async () => {
                  if (!assignTeacherId || !assignSubjectId) return
                  setAssignSaving(true)
                  try {
                    await setTeacherSubjectSections({
                      teacher_id: assignTeacherId,
                      subject_id: assignSubjectId,
                      section_ids: Array.from(assignSelectedSections),
                    })
                    showToast('Assignment saved')
                    await refreshTeacherAssignments(assignTeacherId)
                    await refreshTeacherSubjectSelection(assignTeacherId, assignSubjectId)
                  } catch (e: any) {
                    showToast(`Save failed: ${String(e?.message ?? e)}`, 3500)
                  } finally {
                    setAssignSaving(false)
                  }
                }}
                type="button"
              >
                {assignSaving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>

          <div className="mt-6 rounded-2xl border bg-white p-4">
            <div className="text-xs font-semibold text-slate-800">Current assignments</div>
            {!assignTeacherId ? (
              <div className="mt-3 text-sm text-slate-600">Select a teacher to view assignments.</div>
            ) : assignRows.length === 0 ? (
              <div className="mt-3 text-sm text-slate-600">{assignLoading ? 'Loading…' : 'No assignments yet.'}</div>
            ) : (
              <div className="mt-3 space-y-2">
                {assignRows.map((r) => (
                  <div key={`${r.teacher_id}-${r.subject_id}`} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border bg-slate-50 p-3">
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-slate-900">
                        {r.subject_code} — {r.subject_name}
                      </div>
                      <div className="mt-1 text-xs text-slate-600">
                        Sections: {r.sections.map((s) => s.section_code).join(', ') || '—'}
                      </div>
                    </div>
                    <button
                      className="btn-secondary text-sm font-medium text-slate-800"
                      type="button"
                      onClick={() => {
                        setAssignSubjectId(r.subject_id)
                        setAssignSelectedSections(new Set(r.sections.map((s) => s.section_id)))
                      }}
                    >
                      Edit
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      ) : null}

      {tab === 'manage' ? (
        <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        <section className="rounded-3xl border bg-white p-5">
          <div className="text-sm font-semibold text-slate-900">Add Teacher</div>
          <div className="mt-1 text-xs text-slate-500">
            Fields marked required must be set.
          </div>

          <div className="mt-5 grid gap-3">
            <div>
              <label htmlFor="t_code" className="text-xs font-medium text-slate-600">
                Code (required)
              </label>
              <input
                id="t_code"
                className="input-premium mt-1 w-full text-sm"
                value={form.code}
                onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                placeholder="TCH001"
                autoComplete="off"
              />
            </div>

            <div>
              <label htmlFor="t_name" className="text-xs font-medium text-slate-600">
                Full name (required)
              </label>
              <input
                id="t_name"
                className="input-premium mt-1 w-full text-sm"
                value={form.full_name}
                onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))}
                placeholder="Dr. A. Kumar"
                autoComplete="off"
              />
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor="t_off" className="text-xs font-medium text-slate-600">
                  Weekly Leave (optional)
                </label>
                <div className="mt-1 text-[11px] text-slate-500">
                  Teacher will not be scheduled on this day.
                </div>
                <PremiumSelect
                  id="t_off"
                  ariaLabel="Weekly leave"
                  className="mt-2 text-sm"
                  value={form.weekly_off_day || '__none__'}
                  onValueChange={(v) => setForm((f) => ({ ...f, weekly_off_day: v === '__none__' ? '' : v }))}
                  options={[
                    { value: '__none__', label: 'None' },
                    ...WEEKDAYS.map((d, i) => ({ value: String(i), label: d })),
                  ]}
                />
              </div>

              <div>
                <label htmlFor="t_max_cont" className="text-xs font-medium text-slate-600">
                  Max continuous
                </label>
                <input
                  id="t_max_cont"
                  type="number"
                  className="input-premium mt-1 w-full text-sm"
                  value={form.max_continuous}
                  onChange={(e) => setForm((f) => ({ ...f, max_continuous: Number(e.target.value) }))}
                  min={1}
                />
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor="t_max_day" className="text-xs font-medium text-slate-600">
                  Max/day
                </label>
                <input
                  id="t_max_day"
                  type="number"
                  className="input-premium mt-1 w-full text-sm"
                  value={form.max_per_day}
                  onChange={(e) => setForm((f) => ({ ...f, max_per_day: Number(e.target.value) }))}
                  min={0}
                />
              </div>
              <div>
                <label htmlFor="t_max_week" className="text-xs font-medium text-slate-600">
                  Max/week
                </label>
                <input
                  id="t_max_week"
                  type="number"
                  className="input-premium mt-1 w-full text-sm"
                  value={form.max_per_week}
                  onChange={(e) => setForm((f) => ({ ...f, max_per_week: Number(e.target.value) }))}
                  min={0}
                />
              </div>
            </div>

            <div className="flex items-center justify-between gap-3">
              <label className="flex select-none items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-emerald-600"
                  checked={form.is_active}
                  onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
                />
                Active
              </label>

              <button
                className="btn-primary text-sm font-semibold disabled:opacity-50"
                onClick={onCreate}
                disabled={
                  loading ||
                  !form.code.trim() ||
                    !form.full_name.trim() ||
                    validateCaps(form.max_per_day, form.max_per_week, form.max_continuous).length > 0
                }
              >
                {loading ? 'Saving…' : 'Save Teacher'}
              </button>
            </div>

            <button
              className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
              onClick={() => setForm(DEFAULT_FORM)}
              disabled={loading}
            >
              Reset
            </button>
          </div>
        </section>

        <section className="rounded-3xl border bg-white p-5">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-900">Directory</div>
              <div className="mt-1 text-xs text-slate-500">{items.length} total</div>
            </div>
            <div className="w-full sm:w-72">
              <label htmlFor="t_search" className="text-xs font-medium text-slate-600">
                Search
              </label>
              <input
                id="t_search"
                className="input-premium mt-1 w-full text-sm"
                placeholder="Name, code…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          </div>

          <div className="mt-4 overflow-auto rounded-2xl border">
            <table className="w-full border-collapse bg-white text-sm">
              <thead className="bg-slate-50 text-xs text-slate-600">
                <tr>
                  <th className="px-3 py-2 text-left font-semibold">Code</th>
                  <th className="px-3 py-2 text-left font-semibold">Name</th>
                  <th className="px-3 py-2 text-left font-semibold">Leave</th>
                  <th className="px-3 py-2 text-left font-semibold">Max/day</th>
                  <th className="px-3 py-2 text-left font-semibold">Max/week</th>
                  <th className="px-3 py-2 text-left font-semibold">Load</th>
                  <th className="px-3 py-2 text-left font-semibold">Active</th>
                  <th className="px-3 py-2 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td className="px-3 py-4 text-slate-600" colSpan={8}>
                      No matching teachers.
                    </td>
                  </tr>
                ) : (
                  filtered.map((t) => (
                    <tr key={t.id} className="border-t">
                      <td className="px-3 py-2 font-medium text-slate-900">{t.code}</td>
                      <td className="px-3 py-2">
                        <div className="font-medium text-slate-900">{t.full_name}</div>
                      </td>
                      <td className="px-3 py-2 text-slate-700">
                        {t.weekly_off_day == null ? '—' : WEEKDAYS[t.weekly_off_day]}
                      </td>
                      <td className="px-3 py-2 text-slate-700">{t.max_per_day}</td>
                      <td className="px-3 py-2 text-slate-700">{t.max_per_week}</td>
                      <td className="px-3 py-2">
                        {(() => {
                          const li = loadIndicator(t)
                          return (
                            <span
                              title={li.title}
                              className={
                                'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ' +
                                li.className
                              }
                            >
                              {li.label}
                            </span>
                          )
                        })()}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={
                            'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ' +
                            (t.is_active
                              ? 'bg-emerald-50 text-emerald-700'
                              : 'bg-slate-100 text-slate-600')
                          }
                        >
                          {t.is_active ? 'Active' : 'Inactive'}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          className="mr-2 rounded-xl border bg-white px-3 py-1.5 text-xs font-medium text-slate-800 disabled:opacity-50"
                          onClick={() => openEdit(t)}
                          disabled={loading}
                        >
                          Edit
                        </button>
                        <button
                          className="rounded-xl border bg-white px-3 py-1.5 text-xs font-medium text-slate-800 disabled:opacity-50"
                          onClick={() => onDelete(t.id)}
                          disabled={loading}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
        </div>
      ) : null}
    </div>
  )
}
