import React from 'react'
import { Toast } from '../components/Toast'
import { useLayoutContext } from '../components/Layout'
import { SectionStrengthEditModal } from '../components/SectionStrengthEditModal'
import { PremiumSelect } from '../components/PremiumSelect'
import { useModalScrollLock } from '../hooks/useModalScrollLock'
import {
  createSection,
  deleteSection,
  getSectionTimeWindows,
  listSections,
  putSectionStrength,
  putSectionTimeWindows,
  type Section,
} from '../api/sections'
import { listTimeSlots, type TimeSlot } from '../api/solver'

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

export function Sections() {
  const { programCode, academicYearNumber } = useLayoutContext()
  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [items, setItems] = React.useState<Section[]>([])
  const [query, setQuery] = React.useState('')

  const [editStrengthOpen, setEditStrengthOpen] = React.useState(false)
  const [editStrengthSection, setEditStrengthSection] = React.useState<Section | null>(null)

  const [workingHoursStatusBySectionId, setWorkingHoursStatusBySectionId] = React.useState<
    Record<string, { state: 'ok' | 'missing' | 'unknown'; missingDays?: number[] }>
  >({})

  const [slots, setSlots] = React.useState<TimeSlot[]>([])

  const [workingHoursOpen, setWorkingHoursOpen] = React.useState(false)
  const [workingHoursSection, setWorkingHoursSection] = React.useState<Section | null>(null)
  const [workingHoursByDay, setWorkingHoursByDay] = React.useState<
    Record<number, { start_slot_index: number | null; end_slot_index: number | null }>
  >({})

  useModalScrollLock(workingHoursOpen)

  const [form, setForm] = React.useState({
    code: '',
    name: '',
    strength: 0,
    track: 'CORE',
    is_active: true,
  })
  const [newTrack, setNewTrack] = React.useState('')

  const trackOptions = React.useMemo(() => {
    const set = new Set<string>(DEFAULT_TRACKS)
    for (const s of items) set.add(normalizeTrack(s.track))
    if (form.track) set.add(normalizeTrack(form.track))
    return Array.from(set)
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b))
      .map((t) => ({ value: t, label: t }))
  }, [items, form.track])

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  async function refresh() {
    setLoading(true)
    try {
      const pc = programCode.trim()
      const [data, slotResp] = await Promise.all([
        pc ? listSections({ program_code: pc, academic_year_number: academicYearNumber }) : Promise.resolve([]),
        listTimeSlots(),
      ])
      setItems(data)
      setSlots(slotResp)
      void loadWorkingHoursStatuses(data, slotResp)
    } catch (e: any) {
      showToast(`Load failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  function _activeDaysFromSlots(timeSlots: TimeSlot[]): number[] {
    const set = new Set<number>()
    for (const s of timeSlots) set.add(Number(s.day_of_week))
    return Array.from(set).sort((a, b) => a - b)
  }

  async function loadWorkingHoursStatuses(sections: Section[], timeSlots: TimeSlot[]) {
    const days = _activeDaysFromSlots(timeSlots)
    if (days.length === 0) {
      setWorkingHoursStatusBySectionId({})
      return
    }

    const results = await Promise.allSettled(
      sections.map(async (s) => {
        const resp = await getSectionTimeWindows(s.id)
        const present = new Set(resp.windows.map((w) => Number(w.day_of_week)))
        const missingDays = days.filter((d) => !present.has(d))
        return { sectionId: s.id, missingDays }
      }),
    )

    setWorkingHoursStatusBySectionId((prev) => {
      const next = { ...prev }
      for (let i = 0; i < results.length; i++) {
        const r = results[i]
        const sectionId = sections[i]?.id
        if (!sectionId) continue

        if (r.status === 'fulfilled') {
          if (r.value.missingDays.length > 0) {
            next[sectionId] = { state: 'missing', missingDays: r.value.missingDays }
          } else {
            next[sectionId] = { state: 'ok', missingDays: [] }
          }
        } else {
          next[sectionId] = { state: 'unknown' }
        }
      }
      return next
    })
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
    const track = normalizeTrack(form.track)
    if (!track) {
      showToast('Track is required', 3000)
      return
    }
    setLoading(true)
    try {
      await createSection({
        program_code: pc,
        academic_year_number: academicYearNumber,
        code: form.code.trim(),
        name: form.name.trim(),
        strength: Number(form.strength),
        track,
        is_active: Boolean(form.is_active),
      })
      showToast('Section saved')
      setForm((f) => ({ ...f, code: '', name: '' }))
      await refresh()
    } catch (e: any) {
      showToast(`Save failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function onDelete(id: string) {
    if (!confirm('Delete this section?')) return
    setLoading(true)
    try {
      await deleteSection(id)
      showToast('Section deleted')
      await refresh()
    } catch (e: any) {
      showToast(`Delete failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  function openEditStrength(section: Section) {
    setEditStrengthSection(section)
    setEditStrengthOpen(true)
  }

  function closeEditStrength() {
    setEditStrengthOpen(false)
    setEditStrengthSection(null)
  }

  async function onSaveStrength(payload: { strength: number }) {
    if (!editStrengthSection) return
    setLoading(true)
    try {
      await putSectionStrength(editStrengthSection.id, { strength: Number(payload.strength) })
      showToast('Strength updated')
      closeEditStrength()
      await refresh()
    } catch (e: any) {
      showToast(`Update failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter((s) => s.code.toLowerCase().includes(q) || s.name.toLowerCase().includes(q))
  }, [items, query])

  const slotsByDay = React.useMemo(() => {
    const map = new Map<number, TimeSlot[]>()
    for (const s of slots) {
      const d = Number(s.day_of_week)
      const arr = map.get(d) ?? []
      arr.push(s)
      map.set(d, arr)
    }
    for (const [d, arr] of map.entries()) {
      arr.sort((a, b) => a.slot_index - b.slot_index)
      map.set(d, arr)
    }
    return map
  }, [slots])

  const activeDays = React.useMemo(() => {
    const ds = Array.from(slotsByDay.keys()).sort((a, b) => a - b)
    return ds
  }, [slotsByDay])

  function dayLabel(d: number) {
    return ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][d] ?? `Day ${d}`
  }

  function dayShortLabel(d: number) {
    return ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d] ?? `D${d}`
  }

  async function openWorkingHours(section: Section) {
    setWorkingHoursSection(section)
    setWorkingHoursOpen(true)
    setLoading(true)
    try {
      const current = await getSectionTimeWindows(section.id)
      const next: Record<number, { start_slot_index: number | null; end_slot_index: number | null }> = {}
      for (const d of activeDays) {
        next[d] = { start_slot_index: null, end_slot_index: null }
      }
      for (const w of current.windows) {
        next[w.day_of_week] = { start_slot_index: w.start_slot_index, end_slot_index: w.end_slot_index }
      }
      setWorkingHoursByDay(next)
    } catch (e: any) {
      showToast(`Load working hours failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  function closeWorkingHours() {
    setWorkingHoursOpen(false)
    setWorkingHoursSection(null)
    setWorkingHoursByDay({})
  }

  async function saveWorkingHours() {
    if (!workingHoursSection) return
    if (activeDays.length === 0) {
      showToast('Configure time slots first')
      return
    }

    const windows: Array<{ day_of_week: number; start_slot_index: number; end_slot_index: number }> = []
    for (const d of activeDays) {
      const row = workingHoursByDay[d]
      if (!row || row.start_slot_index == null || row.end_slot_index == null) {
        showToast(`Missing working hours for ${dayLabel(d)}`)
        return
      }
      if (row.end_slot_index < row.start_slot_index) {
        showToast(`End slot must be >= start slot (${dayLabel(d)})`)
        return
      }
      windows.push({ day_of_week: d, start_slot_index: row.start_slot_index, end_slot_index: row.end_slot_index })
    }

    setLoading(true)
    try {
      await putSectionTimeWindows(workingHoursSection.id, { windows })
      {
        const present = new Set(windows.map((w) => Number(w.day_of_week)))
        const missingDays = activeDays.filter((d) => !present.has(d))
        setWorkingHoursStatusBySectionId((prev) => ({
          ...prev,
          [workingHoursSection.id]:
            missingDays.length > 0 ? { state: 'missing', missingDays } : { state: 'ok', missingDays: [] },
        }))
      }
      showToast('Working hours saved')
      closeWorkingHours()
    } catch (e: any) {
      showToast(`Save working hours failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <Toast message={toast} />

      <SectionStrengthEditModal
        open={editStrengthOpen}
        section={editStrengthSection}
        loading={loading}
        onClose={closeEditStrength}
        onSave={onSaveStrength}
      />

      {workingHoursOpen && workingHoursSection ? (
        <div
          className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center z-50 animate-fadeIn p-4"
          onClick={closeWorkingHours}
        >
          <div
            className="w-full max-w-3xl bg-white/80 backdrop-blur-lg rounded-2xl shadow-2xl p-6 border border-white/40 animate-scaleIn"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-lg font-semibold text-slate-900">Working Hours</div>
                <div className="mt-1 text-sm text-slate-600">
                  {workingHoursSection.code} — {workingHoursSection.name}
                </div>
              </div>
              <button
                className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
                onClick={closeWorkingHours}
                disabled={loading}
                type="button"
              >
                Close
              </button>
            </div>

            <div className="mt-4 space-y-3">
              {activeDays.length === 0 ? (
                <div className="rounded-2xl border bg-slate-50 p-4 text-sm text-slate-700">
                  No time slots found. Generate time slots first.
                </div>
              ) : (
                activeDays.map((d) => {
                  const daySlots = slotsByDay.get(d) ?? []
                  const row = workingHoursByDay[d] ?? { start_slot_index: null, end_slot_index: null }
                  return (
                    <div
                      key={d}
                      className="grid items-center gap-3 rounded-2xl border bg-white p-3 md:grid-cols-[160px_1fr_1fr]"
                    >
                      <div className="text-sm font-semibold text-slate-900">{dayLabel(d)}:</div>

                      <div>
                        <div className="text-xs font-semibold text-slate-600">Start Slot</div>
                        <PremiumSelect
                          ariaLabel={`${dayLabel(d)} start slot`}
                          className="mt-1 text-sm"
                          value={row.start_slot_index == null ? '__none__' : String(row.start_slot_index)}
                          onValueChange={(v) => {
                            const next = v === '__none__' ? null : Number(v)
                            setWorkingHoursByDay((prev) => ({
                              ...prev,
                              [d]: { ...prev[d], start_slot_index: next },
                            }))
                          }}
                          options={[
                            { value: '__none__', label: 'Select…' },
                            ...daySlots.map((s) => ({
                              value: String(s.slot_index),
                              label: `#${s.slot_index} (${s.start_time}–${s.end_time})`,
                            })),
                          ]}
                        />
                      </div>

                      <div>
                        <div className="text-xs font-semibold text-slate-600">End Slot</div>
                        <PremiumSelect
                          ariaLabel={`${dayLabel(d)} end slot`}
                          className="mt-1 text-sm"
                          value={row.end_slot_index == null ? '__none__' : String(row.end_slot_index)}
                          onValueChange={(v) => {
                            const next = v === '__none__' ? null : Number(v)
                            setWorkingHoursByDay((prev) => ({
                              ...prev,
                              [d]: { ...prev[d], end_slot_index: next },
                            }))
                          }}
                          options={[
                            { value: '__none__', label: 'Select…' },
                            ...daySlots.map((s) => ({
                              value: String(s.slot_index),
                              label: `#${s.slot_index} (${s.start_time}–${s.end_time})`,
                            })),
                          ]}
                        />
                        {row.start_slot_index != null && row.end_slot_index != null && row.end_slot_index < row.start_slot_index ? (
                          <div className="mt-1 text-xs text-rose-700">End must be ≥ start.</div>
                        ) : null}
                      </div>
                    </div>
                  )
                })
              )}
            </div>

            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
                onClick={closeWorkingHours}
                disabled={loading}
                type="button"
              >
                Cancel
              </button>
              <button
                className="btn-primary text-sm font-semibold disabled:opacity-50"
                onClick={saveWorkingHours}
                disabled={loading || activeDays.length === 0}
                type="button"
              >
                {loading ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-slate-900">Sections</div>
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
          <div className="text-sm font-semibold text-slate-900">Add Section</div>
          <div className="mt-5 grid gap-3">
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor="sec_code" className="text-xs font-medium text-slate-600">
                  Code
                </label>
                <input
                  id="sec_code"
                  className="input-premium mt-1 w-full text-sm"
                  value={form.code}
                  onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                  placeholder="CSE-6A"
                />
              </div>
              <div>
                <label htmlFor="sec_track" className="text-xs font-medium text-slate-600">
                  Track
                </label>
                <PremiumSelect
                  id="sec_track"
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
            </div>

            <div>
              <label htmlFor="sec_name" className="text-xs font-medium text-slate-600">
                Name
              </label>
              <input
                id="sec_name"
                className="input-premium mt-1 w-full text-sm"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Section A"
              />
            </div>

            <div>
              <label htmlFor="sec_strength" className="text-xs font-medium text-slate-600">
                Strength
              </label>
              <input
                id="sec_strength"
                type="number"
                min={0}
                className="input-premium mt-1 w-full text-sm"
                value={form.strength}
                onChange={(e) => setForm((f) => ({ ...f, strength: Number(e.target.value) }))}
              />
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
              {loading ? 'Saving…' : 'Save Section'}
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
              <label htmlFor="sec_search" className="text-xs font-medium text-slate-600">
                Search
              </label>
              <input
                id="sec_search"
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
                  <th className="px-3 py-2 text-left font-semibold">Track</th>
                  <th className="px-3 py-2 text-left font-semibold">Strength</th>
                  <th className="px-3 py-2 text-left font-semibold">Active</th>
                  <th className="px-3 py-2 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td className="px-3 py-4 text-slate-600" colSpan={6}>
                      No matching sections.
                    </td>
                  </tr>
                ) : (
                  filtered.map((s) => (
                    <tr key={s.id} className="border-t">
                      <td className="px-3 py-2 font-medium text-slate-900">{s.code}</td>
                      <td className="px-3 py-2 text-slate-800">
                        <div className="flex flex-wrap items-center gap-2">
                          <span>{s.name}</span>
                          {activeDays.length > 0 && workingHoursStatusBySectionId[s.id]?.state === 'missing' ? (
                            <span
                              className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-700"
                              title={
                                'Missing: ' +
                                (workingHoursStatusBySectionId[s.id]?.missingDays ?? [])
                                  .map((d) => dayLabel(d))
                                  .join(', ')
                              }
                            >
                              Working hours missing ({(workingHoursStatusBySectionId[s.id]?.missingDays ?? []).map(dayShortLabel).join(', ')})
                            </span>
                          ) : null}
                          {activeDays.length > 0 && workingHoursStatusBySectionId[s.id]?.state === 'unknown' ? (
                            <span
                              className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600"
                              title="Could not load working hours status"
                            >
                              Working hours unknown
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-slate-700">{s.track}</td>
                      <td className="px-3 py-2 text-slate-700">{s.strength}</td>
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
                          onClick={() => openWorkingHours(s)}
                          disabled={loading}
                        >
                          Working Hours
                        </button>
                        <button
                          className="btn-secondary mr-2 text-xs font-medium text-slate-800 disabled:opacity-50"
                          onClick={() => openEditStrength(s)}
                          disabled={loading}
                        >
                          Edit Strength
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
