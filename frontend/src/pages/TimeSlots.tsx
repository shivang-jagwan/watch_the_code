import React from 'react'
import { Toast } from '../components/Toast'
import { clearTimetables, generateTimeSlots, toggleLunchBreak } from '../api/admin'
import { listTimeSlots, TimeSlot } from '../api/solver'

const WEEKDAYS = [
  { label: 'Mon', value: 0 },
  { label: 'Tue', value: 1 },
  { label: 'Wed', value: 2 },
  { label: 'Thu', value: 3 },
  { label: 'Fri', value: 4 },
  { label: 'Sat', value: 5 },
]

function byDayThenIndex(a: TimeSlot, b: TimeSlot) {
  if (a.day_of_week !== b.day_of_week) return a.day_of_week - b.day_of_week
  return a.slot_index - b.slot_index
}

export function TimeSlots() {
  const [toast, setToast] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [slots, setSlots] = React.useState<TimeSlot[]>([])

  const [days, setDays] = React.useState<number[]>([0, 1, 2, 3, 4, 5])
  const [startTime, setStartTime] = React.useState('09:00')
  const [endTime, setEndTime] = React.useState('17:00')
  const [slotMinutes, setSlotMinutes] = React.useState(60)
  const [replaceExisting, setReplaceExisting] = React.useState(false)

  function showToast(message: string, ms = 2500) {
    setToast(message)
    window.setTimeout(() => setToast(''), ms)
  }

  async function refresh() {
    setLoading(true)
    try {
      const data = await listTimeSlots()
      setSlots(data.slice().sort(byDayThenIndex))
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

  async function onGenerate() {
    if (days.length === 0) {
      showToast('Select at least one day', 3000)
      return
    }

    setLoading(true)
    try {
      const result = await generateTimeSlots({
        days,
        start_time: startTime,
        end_time: endTime,
        slot_minutes: Number(slotMinutes),
        replace_existing: Boolean(replaceExisting),
      })
      if (result.ok) {
        const parts = [
          result.created ? `created ${result.created}` : null,
          result.updated ? `updated ${result.updated}` : null,
          result.deleted ? `deleted ${result.deleted}` : null,
        ].filter(Boolean)
        showToast(parts.length ? `Time slots: ${parts.join(', ')}` : 'Time slots updated')
      } else {
        showToast(result.message || 'Generate failed', 3500)
      }
      await refresh()
    } catch (e: any) {
      showToast(`Generate failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  async function onClearTimetables() {
    const ok = window.confirm(
      'This will delete existing timetable runs/entries so you can replace time slots. Continue?',
    )
    if (!ok) return

    setLoading(true)
    try {
      const result = await clearTimetables({ confirm: 'DELETE' })
      if (result.ok) {
        const parts = [
          result.deleted ? `deleted ${result.deleted}` : null,
        ].filter(Boolean)
        showToast(parts.length ? `Cleared: ${parts.join(', ')}` : 'Cleared timetables')
      } else {
        showToast(result.message || 'Clear failed', 3500)
      }
      await refresh()
    } catch (e: any) {
      showToast(`Clear failed: ${String(e?.message ?? e)}`, 3500)
    } finally {
      setLoading(false)
    }
  }

  const summary = React.useMemo(() => {
    const byDay = new Map<number, number>()
    for (const s of slots) byDay.set(s.day_of_week, (byDay.get(s.day_of_week) ?? 0) + 1)
    return WEEKDAYS.map((d) => ({
      ...d,
      count: byDay.get(d.value) ?? 0,
    }))
  }, [slots])

  return (
    <div className="space-y-6">
      <Toast message={toast} />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-slate-900">Time Slots</div>
          <div className="mt-1 text-sm text-slate-600">
            Generate the day/period grid used by the solver and timetable viewer.
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn-secondary text-sm font-medium text-slate-800 disabled:opacity-50"
            onClick={refresh}
            disabled={loading}
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        <section className="rounded-3xl border bg-white p-5">
          <div className="text-sm font-semibold text-slate-900">Generate Slots</div>
          <div className="mt-1 text-xs text-slate-500">
            This will create/update slots for selected days.
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              className="btn-danger text-xs font-semibold disabled:opacity-50"
              onClick={onClearTimetables}
              disabled={loading}
              title="Deletes timetable runs/entries so Replace existing can work"
            >
              Clear timetables
            </button>
          </div>

          <div className="mt-5 space-y-4">
            <div>
              <div className="text-xs font-medium text-slate-600">Days</div>
              <div className="mt-2 grid grid-cols-3 gap-2">
                {WEEKDAYS.map((d) => {
                  const checked = days.includes(d.value)
                  return (
                    <label
                      key={d.value}
                      className={
                        'flex cursor-pointer select-none items-center justify-between gap-2 rounded-2xl border px-3 py-2 text-sm ' +
                        (checked ? 'border-slate-900 bg-slate-900 text-white' : 'bg-white text-slate-800')
                      }
                    >
                      <span className="font-medium">{d.label}</span>
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-emerald-600"
                        checked={checked}
                        onChange={(e) => {
                          const next = e.target.checked
                            ? Array.from(new Set([...days, d.value])).sort((a, b) => a - b)
                            : days.filter((x) => x !== d.value)
                          setDays(next)
                        }}
                      />
                    </label>
                  )
                })}
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor="ts_start" className="text-xs font-medium text-slate-600">
                  Start time
                </label>
                <input
                  id="ts_start"
                  type="time"
                  className="input-premium mt-1 w-full text-sm"
                  value={startTime}
                  onChange={(e) => setStartTime(e.target.value)}
                />
              </div>
              <div>
                <label htmlFor="ts_end" className="text-xs font-medium text-slate-600">
                  End time
                </label>
                <input
                  id="ts_end"
                  type="time"
                  className="input-premium mt-1 w-full text-sm"
                  value={endTime}
                  onChange={(e) => setEndTime(e.target.value)}
                />
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label htmlFor="ts_minutes" className="text-xs font-medium text-slate-600">
                  Slot minutes
                </label>
                <input
                  id="ts_minutes"
                  type="number"
                  min={15}
                  max={240}
                  className="input-premium mt-1 w-full text-sm"
                  value={slotMinutes}
                  onChange={(e) => setSlotMinutes(Number(e.target.value))}
                />
                <div className="mt-1 text-xs text-slate-500">Allowed: 15–240</div>
              </div>

              <div className="flex items-end">
                <label className="flex select-none items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-emerald-600"
                    checked={replaceExisting}
                    onChange={(e) => setReplaceExisting(e.target.checked)}
                  />
                  Replace existing (delete all first)
                </label>
              </div>
            </div>

            <button
              className="btn-primary w-full text-sm font-semibold disabled:opacity-50"
              onClick={onGenerate}
              disabled={loading}
            >
              {loading ? 'Generating…' : 'Generate Time Slots'}
            </button>

            <div className="rounded-2xl border bg-slate-50 p-3 text-xs text-slate-600">
              Tip: If solver says “No time slots configured”, generate slots here.
            </div>
          </div>
        </section>

        <section className="rounded-3xl border bg-white p-5">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-900">Configured Slots</div>
              <div className="mt-1 text-xs text-slate-500">{slots.length} total</div>
            </div>
            <div className="flex flex-wrap gap-2">
              {summary.map((d) => (
                <div
                  key={d.value}
                  className={
                    'rounded-full border px-3 py-1 text-xs font-semibold ' +
                    (d.count ? 'bg-white text-slate-800' : 'bg-slate-50 text-slate-500')
                  }
                >
                  {d.label}: {d.count}
                </div>
              ))}
            </div>
          </div>

          <div className="mt-4 overflow-auto rounded-2xl border">
            <table className="w-full border-collapse bg-white text-sm">
              <thead className="bg-slate-50 text-xs text-slate-600">
                <tr>
                  <th className="px-3 py-2 text-left font-semibold">Day</th>
                  <th className="px-3 py-2 text-left font-semibold">Index</th>
                  <th className="px-3 py-2 text-left font-semibold">Start</th>
                  <th className="px-3 py-2 text-left font-semibold">End</th>
                  <th className="px-3 py-2 text-left font-semibold">Lunch / Break</th>
                  <th className="px-3 py-2 text-left font-semibold">ID</th>
                </tr>
              </thead>
              <tbody>
                {slots.length === 0 ? (
                  <tr>
                    <td className="px-3 py-4 text-slate-600" colSpan={6}>
                      No time slots configured.
                    </td>
                  </tr>
                ) : (
                  slots.map((s) => (
                    <tr key={s.id} className={`border-t ${s.is_lunch_break ? 'bg-amber-50' : ''}`}>
                      <td className="px-3 py-2 font-medium text-slate-900">
                        {WEEKDAYS.find((d) => d.value === s.day_of_week)?.label ?? String(s.day_of_week)}
                      </td>
                      <td className="px-3 py-2 text-slate-700">{s.slot_index}</td>
                      <td className="px-3 py-2 text-slate-700">{s.start_time}</td>
                      <td className="px-3 py-2 text-slate-700">{s.end_time}</td>
                      <td className="px-3 py-2">
                        <button
                          className={`rounded-full px-2 py-0.5 text-xs font-semibold transition-colors ${
                            s.is_lunch_break
                              ? 'bg-amber-200 text-amber-800 hover:bg-amber-300'
                              : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                          }`}
                          disabled={loading}
                          onClick={async () => {
                            setLoading(true)
                            try {
                              await toggleLunchBreak(s.id)
                              await refresh()
                            } catch (e: any) {
                              showToast(`Toggle failed: ${String(e?.message ?? e)}`, 3500)
                              setLoading(false)
                            }
                          }}
                          title={s.is_lunch_break ? 'Click to unmark lunch/break' : 'Click to mark as lunch/break'}
                        >
                          {s.is_lunch_break ? 'Lunch/Break' : 'Mark'}
                        </button>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-slate-500">{s.id}</td>
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
