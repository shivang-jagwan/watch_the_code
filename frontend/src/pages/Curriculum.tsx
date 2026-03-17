import React from 'react'
import { Toast } from '../components/Toast'
import { useLayoutContext } from '../components/Layout'
import { createTrackSubject, deleteTrackSubject, listTrackSubjects, TrackSubject } from '../api/curriculum'
import { listSubjects, Subject } from '../api/subjects'
import { PremiumSelect } from '../components/PremiumSelect'

const DEFAULT_TRACKS = ['CORE', 'CYBER', 'AI_DS', 'AI_ML']

function normalizeTrack(input: string): string {
  return String(input)
    .trim()
    .toUpperCase()
    .replace(/\s+/g, '_')
    .replace(/[^A-Z0-9_]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
}

export function Curriculum() {
  const { programCode, academicYearNumber } = useLayoutContext()
  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)

  const [subjects, setSubjects] = React.useState<Subject[]>([])
  const [rows, setRows] = React.useState<TrackSubject[]>([])

  const [form, setForm] = React.useState({
    track: 'CORE',
    subject_code: '',
    is_elective: false,
    sessions_override: '',
  })
  const [newTrack, setNewTrack] = React.useState('')

  const trackOptions = React.useMemo(() => {
    const set = new Set<string>(DEFAULT_TRACKS)
    for (const r of rows) set.add(normalizeTrack(r.track))
    if (form.track) set.add(normalizeTrack(form.track))
    return Array.from(set)
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b))
      .map((t) => ({ value: t, label: t }))
  }, [rows, form.track])

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  async function refresh() {
    setLoading(true)
    try {
      const pc = programCode.trim()
      if (!pc) {
        setSubjects([])
        setRows([])
        return
      }
      const [subs, ts] = await Promise.all([
        listSubjects({ program_code: pc, academic_year_number: academicYearNumber }),
        listTrackSubjects({ program_code: pc, academic_year_number: academicYearNumber }),
      ])
      setSubjects(subs)
      setRows(ts)
      if (!form.subject_code && subs.length) {
        setForm((f) => ({ ...f, subject_code: subs[0].code }))
      }
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

  async function onAdd() {
    const pc = programCode.trim()
    if (!pc) {
      showToast('Select a program first', 3000)
      return
    }
    const track = normalizeTrack(form.track)
    if (!track) {
      showToast('Track is required', 3000)
      return
    }
    setLoading(true)
    try {
      await createTrackSubject({
        program_code: pc,
        academic_year_number: academicYearNumber,
        track,
        subject_code: form.subject_code,
        is_elective: Boolean(form.is_elective),
        sessions_override: form.sessions_override === '' ? null : Number(form.sessions_override),
      })
      showToast('Mapping saved')
      await refresh()
    } catch (e: any) {
      showToast(`Save failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function onDelete(id: string) {
    if (!confirm('Delete this mapping?')) return
    setLoading(true)
    try {
      await deleteTrackSubject(id)
      showToast('Mapping deleted')
      await refresh()
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  const subjectNameById = React.useMemo(() => {
    const map = new Map<string, string>()
    for (const s of subjects) map.set(s.id, `${s.code} • ${s.name}`)
    return map
  }, [subjects])

  return (
    <div className="space-y-6">
      <Toast message={toast} />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-slate-900">Curriculum (Track Mapping)</div>
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
          <div className="text-sm font-semibold text-slate-900">Add Mapping</div>
          <div className="mt-5 grid gap-3">
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor="cur_track" className="text-xs font-medium text-slate-600">
                  Track
                </label>
                <PremiumSelect
                  id="cur_track"
                  ariaLabel="Track"
                  className="mt-1 text-sm"
                  value={form.track}
                  onValueChange={(v) => setForm((f) => ({ ...f, track: v }))}
                  options={trackOptions}
                />
                <div className="mt-2 flex gap-2">
                  <input
                    className="input-premium w-full text-sm"
                    value={newTrack}
                    onChange={(e) => setNewTrack(e.target.value)}
                    placeholder="Add new track (e.g., CYBER_SECURITY)"
                  />
                  <button
                    type="button"
                    className="btn-secondary text-xs font-semibold"
                    onClick={() => {
                      const t = normalizeTrack(newTrack)
                      if (!t) {
                        showToast('Enter a valid track', 3000)
                        return
                      }
                      setForm((f) => ({ ...f, track: t }))
                      setNewTrack('')
                    }}
                  >
                    Use
                  </button>
                </div>
              </div>
              <div>
                <label htmlFor="cur_sub" className="text-xs font-medium text-slate-600">
                  Subject
                </label>
                <PremiumSelect
                  id="cur_sub"
                  ariaLabel="Subject"
                  className="mt-1 text-sm"
                  value={form.subject_code || '__none__'}
                  onValueChange={(v) => setForm((f) => ({ ...f, subject_code: v === '__none__' ? '' : v }))}
                  options={[
                    { value: '__none__', label: 'Select…' },
                    ...subjects.map((s) => ({ value: s.code, label: `${s.code} • ${s.name}` })),
                  ]}
                />
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <label className="flex select-none items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-emerald-600"
                  checked={form.is_elective}
                  onChange={(e) => setForm((f) => ({ ...f, is_elective: e.target.checked }))}
                />
                Mark as elective
              </label>

              <div>
                <label htmlFor="cur_override" className="text-xs font-medium text-slate-600">
                  Sessions override
                </label>
                <input
                  id="cur_override"
                  type="number"
                  min={0}
                  className="input-premium mt-1 w-full text-sm"
                  value={form.sessions_override}
                  onChange={(e) => setForm((f) => ({ ...f, sessions_override: e.target.value }))}
                  placeholder="(optional)"
                />
              </div>
            </div>

            <button
              className="btn-primary text-sm font-semibold disabled:opacity-50"
              onClick={onAdd}
              disabled={loading || !form.subject_code}
            >
              {loading ? 'Saving…' : 'Save Mapping'}
            </button>
          </div>
        </section>

        <section className="rounded-3xl border bg-white p-5">
          <div className="text-sm font-semibold text-slate-900">Mappings</div>
          <div className="mt-1 text-xs text-slate-500">{rows.length} total</div>

          <div className="mt-4 overflow-auto rounded-2xl border">
            <table className="w-full border-collapse bg-white text-sm">
              <thead className="bg-slate-50 text-xs text-slate-600">
                <tr>
                  <th className="px-3 py-2 text-left font-semibold">Track</th>
                  <th className="px-3 py-2 text-left font-semibold">Subject</th>
                  <th className="px-3 py-2 text-left font-semibold">Elective</th>
                  <th className="px-3 py-2 text-left font-semibold">Override</th>
                  <th className="px-3 py-2 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td className="px-3 py-4 text-slate-600" colSpan={5}>
                      No curriculum mappings yet.
                    </td>
                  </tr>
                ) : (
                  rows.map((r) => (
                    <tr key={r.id} className="border-t">
                      <td className="px-3 py-2 font-medium text-slate-900">{r.track}</td>
                      <td className="px-3 py-2 text-slate-800">
                        {subjectNameById.get(r.subject_id) ?? r.subject_id}
                      </td>
                      <td className="px-3 py-2 text-slate-700">{r.is_elective ? 'Yes' : 'No'}</td>
                      <td className="px-3 py-2 text-slate-700">
                        {r.sessions_override == null ? '—' : r.sessions_override}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          className="btn-danger text-xs font-semibold disabled:opacity-50"
                          onClick={() => onDelete(r.id)}
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
