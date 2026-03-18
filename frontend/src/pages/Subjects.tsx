import React from 'react'
import { Toast } from '../components/Toast'
import { useLayoutContext } from '../components/Layout'
import { PremiumSelect } from '../components/PremiumSelect'
import {
  createSubject,
  deleteSubject,
  listSubjects,
  Subject,
  SubjectPut,
  updateSubject,
} from '../api/subjects'
import { SubjectEditModal } from '../components/SubjectEditModal'

const SUBJECT_TYPES = [
  { label: 'Theory', value: 'THEORY' },
  { label: 'Lab', value: 'LAB' },
]

export function Subjects() {
  const { programCode, academicYearNumber } = useLayoutContext()
  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [items, setItems] = React.useState<Subject[]>([])
  const [query, setQuery] = React.useState('')

  const [editOpen, setEditOpen] = React.useState(false)
  const [editSubject, setEditSubject] = React.useState<Subject | null>(null)
  const [editSaving, setEditSaving] = React.useState(false)

  const [form, setForm] = React.useState({
    code: '',
    name: '',
    subject_type: 'THEORY',
    sessions_per_week: 4,
    max_per_day: 1,
    lab_block_size_slots: 1,
    is_active: true,
  })

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  function validateSubjectPayload(payload: {
    subject_type: string
    sessions_per_week: number
    max_per_day: number
    lab_block_size_slots: number
  }): string[] {
    const errors: string[] = []
    const st = String(payload.subject_type).toUpperCase()

    if (payload.sessions_per_week < 1) errors.push('Sessions/week must be >= 1')
    if (payload.sessions_per_week > 6) errors.push('Sessions/week cannot exceed 6')

    if (payload.max_per_day < 1) errors.push('Max/day must be >= 1')
    if (payload.max_per_day > payload.sessions_per_week) errors.push('Max/day must be <= Sessions/week')

    if (st === 'THEORY') {
      if (payload.lab_block_size_slots !== 1) errors.push('For THEORY, Lab block size must be 1')
    } else if (st === 'LAB') {
      if (payload.lab_block_size_slots < 2) errors.push('For LAB, Lab block size must be >= 2')
    } else {
      errors.push('Type must be THEORY or LAB')
    }

    if (payload.sessions_per_week * payload.lab_block_size_slots > 12) {
      errors.push('Sessions/week × Lab block size cannot exceed 12')
    }

    return errors
  }

  function effectiveWeeklyLoad(s: Subject): number {
    const spw = Number(s.sessions_per_week ?? 0)
    if (String(s.subject_type).toUpperCase() === 'LAB') {
      return spw * Number(s.lab_block_size_slots ?? 1)
    }
    return spw
  }

  function openEdit(s: Subject) {
    setEditSubject(s)
    setEditOpen(true)
  }

  function closeEdit() {
    if (editSaving) return
    setEditOpen(false)
    setEditSubject(null)
  }

  async function refresh() {
    setLoading(true)
    try {
      const pc = programCode.trim()
      if (!pc) {
        setItems([])
        return
      }
      const data = await listSubjects({ program_code: pc, academic_year_number: academicYearNumber })
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
  }, [programCode, academicYearNumber])

  async function onCreate() {
    const pc = programCode.trim()
    if (!pc) {
      showToast('Select a program first', 3000)
      return
    }
    const next = {
      subject_type: form.subject_type,
      sessions_per_week: Number(form.sessions_per_week),
      max_per_day: Number(form.max_per_day),
      lab_block_size_slots:
        String(form.subject_type).toUpperCase() === 'THEORY' ? 1 : Number(form.lab_block_size_slots),
    }
    const errors = validateSubjectPayload(next)
    if (errors.length) {
      showToast(errors[0], 3500)
      return
    }

    setLoading(true)
    try {
      await createSubject({
        program_code: pc,
        academic_year_number: academicYearNumber,
        code: form.code.trim(),
        name: form.name.trim(),
        subject_type: next.subject_type,
        sessions_per_week: next.sessions_per_week,
        max_per_day: next.max_per_day,
        lab_block_size_slots: next.lab_block_size_slots,
        is_active: Boolean(form.is_active),
      })
      showToast('Subject saved')
      setForm((f) => ({ ...f, code: '', name: '' }))
      await refresh()
    } catch (e: any) {
      showToast(`Save failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function onSaveEdit(payload: SubjectPut) {
    if (!editSubject) return

    const normalized: SubjectPut = {
      ...payload,
      subject_type: String(payload.subject_type).toUpperCase(),
      lab_block_size_slots:
        String(payload.subject_type).toUpperCase() === 'THEORY' ? 1 : payload.lab_block_size_slots,
    }
    const errors = validateSubjectPayload({
      subject_type: normalized.subject_type,
      sessions_per_week: Number(normalized.sessions_per_week),
      max_per_day: Number(normalized.max_per_day),
      lab_block_size_slots: Number(normalized.lab_block_size_slots),
    })
    if (errors.length) {
      showToast(errors[0], 3500)
      return
    }

    setEditSaving(true)
    try {
      await updateSubject(editSubject.id, normalized)
      showToast('Subject updated')
      closeEdit()
      await refresh()
    } catch (e: any) {
      showToast(`Update failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setEditSaving(false)
    }
  }

  async function onDelete(id: string) {
    if (!confirm('Delete this subject and all dependent data? This cannot be undone.')) return
    setLoading(true)
    try {
      await deleteSubject(id, true)
      showToast('Subject deleted')
      await refresh()
    } catch (e: any) {
      const msg = String(e?.message ?? e)
      if (msg.includes('SUBJECT_NOT_FOUND')) {
        // Subject may already be deleted in another tab/session; sync UI state.
        showToast('Subject already removed')
        await refresh()
      } else {
        showToast(`Delete failed: ${msg}`, 3500)
      }
    } finally {
      setLoading(false)
    }
  }

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter((s) => s.code.toLowerCase().includes(q) || s.name.toLowerCase().includes(q))
  }, [items, query])

  return (
    <div className="space-y-6">
      <Toast message={toast} />

      <SubjectEditModal
        open={editOpen}
        subject={editSubject}
        loading={editSaving}
        onClose={closeEdit}
        onSave={onSaveEdit}
      />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-slate-900">Subjects</div>
          <div className="mt-1 text-sm text-slate-600">
            Program <span className="font-semibold">{programCode}</span> • Year{' '}
            <span className="font-semibold">{academicYearNumber}</span>
          </div>
        </div>
        <button
          className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
          onClick={refresh}
          disabled={loading}
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        <section className="rounded-3xl border bg-white p-5">
          <div className="text-sm font-semibold text-slate-900">Add Subject</div>
          <div className="mt-5 grid gap-3">
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor="sub_code" className="text-xs font-medium text-slate-600">
                  Code
                </label>
                <input
                  id="sub_code"
                  className="input-premium mt-1 w-full text-sm"
                  value={form.code}
                  onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                  placeholder="CS601"
                />
              </div>
              <div>
                <label htmlFor="sub_type" className="text-xs font-medium text-slate-600">
                  Type
                </label>
                <PremiumSelect
                  id="sub_type"
                  ariaLabel="Subject type"
                  className="mt-1 text-sm"
                  value={form.subject_type}
                  onValueChange={(nextType) => {
                    setForm((f) => ({
                      ...f,
                      subject_type: nextType,
                      lab_block_size_slots:
                        String(nextType).toUpperCase() === 'THEORY'
                          ? 1
                          : Math.max(2, Number(f.lab_block_size_slots ?? 2)),
                    }))
                  }}
                  options={SUBJECT_TYPES.map((t) => ({ value: t.value, label: t.label }))}
                />
              </div>
            </div>

            <div>
              <label htmlFor="sub_name" className="text-xs font-medium text-slate-600">
                Name
              </label>
              <input
                id="sub_name"
                className="input-premium mt-1 w-full text-sm"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Operating Systems"
              />
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <label htmlFor="sub_spw" className="text-xs font-medium text-slate-600">
                  Sessions/week
                </label>
                <input
                  id="sub_spw"
                  type="number"
                  min={1}
                  max={6}
                  className="input-premium mt-1 w-full text-sm"
                  value={form.sessions_per_week}
                  onChange={(e) => setForm((f) => ({ ...f, sessions_per_week: Number(e.target.value) }))}
                />
              </div>
              <div>
                <label htmlFor="sub_mpd" className="text-xs font-medium text-slate-600">
                  Max/day
                </label>
                <input
                  id="sub_mpd"
                  type="number"
                  min={1}
                  className="input-premium mt-1 w-full text-sm"
                  value={form.max_per_day}
                  onChange={(e) => setForm((f) => ({ ...f, max_per_day: Number(e.target.value) }))}
                />
              </div>
              <div>
                <label htmlFor="sub_lab" className="text-xs font-medium text-slate-600">
                  Lab block slots
                </label>
                <input
                  id="sub_lab"
                  type="number"
                  min={String(form.subject_type).toUpperCase() === 'LAB' ? 2 : 1}
                  className={
                    'input-premium mt-1 w-full text-sm ' +
                    (String(form.subject_type).toUpperCase() === 'THEORY' ? 'bg-slate-50 text-slate-700' : '')
                  }
                  value={form.lab_block_size_slots}
                  onChange={(e) => setForm((f) => ({ ...f, lab_block_size_slots: Number(e.target.value) }))}
                  disabled={String(form.subject_type).toUpperCase() === 'THEORY'}
                />
                {String(form.subject_type).toUpperCase() === 'THEORY' && (
                  <div className="mt-1 text-[11px] text-slate-500">THEORY uses block size 1.</div>
                )}
              </div>
            </div>

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
              disabled={loading || !form.code.trim() || !form.name.trim()}
            >
              {loading ? 'Saving…' : 'Save Subject'}
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
              <label htmlFor="sub_search" className="text-xs font-medium text-slate-600">
                Search
              </label>
              <input
                id="sub_search"
                className="input-premium mt-1 w-full text-sm"
                placeholder="Code or name…"
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
                  <th className="px-3 py-2 text-left font-semibold">Type</th>
                  <th className="px-3 py-2 text-left font-semibold">/week</th>
                  <th className="px-3 py-2 text-left font-semibold">Max/day</th>
                  <th className="px-3 py-2 text-left font-semibold">Lab block</th>
                  <th className="px-3 py-2 text-left font-semibold">Effective Weekly Load</th>
                  <th className="px-3 py-2 text-left font-semibold">Active</th>
                  <th className="px-3 py-2 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td className="px-3 py-4 text-slate-600" colSpan={9}>
                      No matching subjects.
                    </td>
                  </tr>
                ) : (
                  filtered.map((s) => (
                    <tr key={s.id} className="border-t">
                      <td className="px-3 py-2 font-medium text-slate-900">{s.code}</td>
                      <td className="px-3 py-2 text-slate-800">{s.name}</td>
                      <td className="px-3 py-2 text-slate-700">{s.subject_type}</td>
                      <td className="px-3 py-2 text-slate-700">{s.sessions_per_week}</td>
                      <td className="px-3 py-2 text-slate-700">{s.max_per_day}</td>
                      <td className="px-3 py-2 text-slate-700">{s.lab_block_size_slots}</td>
                      <td className="px-3 py-2">
                        <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-700">
                          {effectiveWeeklyLoad(s)}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={
                            'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ' +
                            (s.is_active
                              ? 'bg-emerald-50 text-emerald-700'
                              : 'bg-slate-100 text-slate-600')
                          }
                        >
                          {s.is_active ? 'Active' : 'Inactive'}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          className="btn-secondary mr-2 text-xs font-medium text-slate-800 disabled:opacity-50"
                          onClick={() => openEdit(s)}
                          disabled={loading}
                        >
                          Edit
                        </button>
                        <button
                          className="btn-danger text-xs font-semibold disabled:opacity-50"
                          onClick={() => onDelete(s.id)}
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
    </div>
  )
}
