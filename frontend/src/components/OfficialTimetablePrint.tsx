/**
 * OfficialTimetablePrint — unified official university timetable format.
 *
 * A4 landscape, Times New Roman, bordered grid, subject legend, footer.
 * Works for Section / Faculty / Room timetables.
 *
 * Usage:
 *   1. Render <style>{OFFICIAL_PRINT_STYLES}</style> once on the page.
 *   2. Render <OfficialTimetablePrint ... /> for each timetable block.
 *
 * For bulk printing, wrap each block with pageBreak={true} (except the last).
 */

import React from 'react'
import { type TimeSlot } from '../api/solver'

// ─── types ────────────────────────────────────────────────────────────────────

/** Normalized entry — used internally and by adapter functions. */
export type OfficialEntry = {
  day: number
  slotIndex: number
  startTime: string
  endTime: string
  subjectCode: string
  /** Full subject name; fall back to subjectCode when not available. */
  subjectName: string
  teacherName: string
  roomCode: string
  /** Section identifier; empty string when not applicable. */
  sectionCode: string
  electiveBlockId?: string | null
  electiveBlockName?: string | null
}

export type OfficialTimetablePrintProps = {
  /** Determines how cell lines are ordered and what the legend 3rd col shows. */
  type: 'section' | 'faculty' | 'room'
  /** Section name, teacher full name, or room code */
  title: string
  programCode?: string
  semester?: string
  effectiveDate?: string
  /** Optional "Class co-ordinator" line in footer (section view only) */
  coordinator?: string
  entries: OfficialEntry[]
  slots: TimeSlot[]
  /**
   * Apply CSS page-break-after:always so each block starts on a new printed
   * page.  Set to true for every block except the last in a bulk list.
   */
  pageBreak?: boolean
}

// ─── adapters ─────────────────────────────────────────────────────────────────

/**
 * Adapt a TimetableEntry (from listRunEntries / api/solver) to OfficialEntry.
 * This is the rich entry type that includes subject_name and subject_type.
 */
export function adaptTimetableEntry(e: {
  day_of_week: number
  slot_index: number
  start_time: string
  end_time: string
  subject_code: string
  subject_name: string
  teacher_name: string
  room_code: string
  section_code: string
  elective_block_id?: string | null
  elective_block_name?: string | null
}): OfficialEntry {
  return {
    day: e.day_of_week,
    slotIndex: e.slot_index,
    startTime: e.start_time,
    endTime: e.end_time,
    subjectCode: e.subject_code,
    subjectName: e.subject_name || e.subject_code,
    teacherName: e.teacher_name,
    roomCode: e.room_code,
    sectionCode: e.section_code,
    electiveBlockId: e.elective_block_id ?? null,
    electiveBlockName: e.elective_block_name ?? null,
  }
}

/**
 * Adapt a TimetableGridEntry (from getSectionTimetable / getRoomTimetable /
 * getFacultyTimetable) to OfficialEntry.  This type lacks subject_name so
 * subject_code is used as a fallback.
 */
export function adaptGridEntry(e: {
  day: number
  slot_index: number
  start_time: string
  end_time: string
  subject_code: string
  teacher_name: string
  room_code: string
  section_code: string
  elective_block_id?: string | null
  elective_block_name?: string | null
}): OfficialEntry {
  return {
    day: e.day,
    slotIndex: e.slot_index,
    startTime: e.start_time,
    endTime: e.end_time,
    subjectCode: e.subject_code,
    subjectName: e.subject_code,
    teacherName: e.teacher_name,
    roomCode: e.room_code,
    sectionCode: e.section_code,
    electiveBlockId: e.elective_block_id ?? null,
    electiveBlockName: e.elective_block_name ?? null,
  }
}

// ─── shared CSS (inject once per page) ───────────────────────────────────────

export const OFFICIAL_PRINT_STYLES = `
  /* ── screen base ─────────────────────────────────────────────────────── */
  .opt-table {
    border-collapse: collapse;
    width: 100%;
    font-family: 'Times New Roman', Times, serif;
    font-size: 11px;
  }
  .opt-table th,
  .opt-table td {
    border: 1px solid #374151;
    padding: 4px 6px;
    vertical-align: middle;
  }
  .opt-header-row th {
    background: #f1f5f9;
    font-weight: 700;
    text-align: center;
    white-space: nowrap;
  }
  .opt-cell-day {
    background: #f1f5f9;
    font-weight: 700;
    text-align: center;
    white-space: nowrap;
    min-width: 44px;
  }
  .opt-cell { text-align: center; min-width: 88px; }
  .opt-cell-lab { background: #eff6ff; }
  .opt-cell-empty { text-align: center; color: #cbd5e1; }
  .opt-elective-label {
    font-size: 9px; font-weight: 700;
    text-transform: uppercase; color: #4338ca;
    margin-bottom: 2px;
  }
  .opt-cell-divider {
    border-top: 1px dashed #c7d2fe;
    padding-top: 2px; margin-top: 2px;
  }
  .opt-line1 { font-weight: 600; }
  .opt-line2 { font-size: 10px; color: #374151; }
  .opt-line3 { font-size: 10px; color: #374151; }
  .opt-legend-title {
    font-family: 'Times New Roman', Times, serif;
    font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.05em;
    margin-bottom: 4px; color: #1e293b;
  }

  /* ── print overrides ─────────────────────────────────────────────────── */
  @media print {
    .no-print { display: none !important; }

    @page {
      size: A4 landscape;
      margin: 1cm 1.2cm;
    }

    body {
      font-family: 'Times New Roman', Times, serif !important;
      font-size: 11px !important;
      color: #000 !important;
      background: #fff !important;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }

    .opt-table { font-size: 10px !important; }
    .opt-table th,
    .opt-table td {
      border: 1px solid #000 !important;
      padding: 2px 4px !important;
    }
    .opt-cell { min-width: unset; }
    .opt-cell-lab  { background: #dbeafe !important; }
    .opt-cell-day  { background: #f1f5f9 !important; }
    .opt-header-row th { background: #f1f5f9 !important; }
    .opt-line2, .opt-line3 { font-size: 9px !important; }
    .opt-elective-label { font-size: 8px !important; }

    .opt-page-break {
      page-break-after: always;
      break-after: always;
    }
  }
`

// ─── helpers ──────────────────────────────────────────────────────────────────

const DAYS_SHORT = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']

function fmtTime(t: string): string {
  return t ? t.slice(0, 5) : ''
}

function ck(day: number, si: number): string {
  return `${day}:${si}`
}

type ElectiveGroup = { name: string; items: OfficialEntry[] }

function groupElective(entries: OfficialEntry[]): {
  nonElective: OfficialEntry[]
  electiveGroups: ElectiveGroup[]
} {
  const nonElective: OfficialEntry[] = []
  const byBlock = new Map<string, ElectiveGroup>()
  for (const e of entries) {
    if (!e.electiveBlockId) {
      nonElective.push(e)
      continue
    }
    const name = String(e.electiveBlockName ?? 'Elective')
    const g = byBlock.get(e.electiveBlockId) ?? { name, items: [] }
    g.items.push(e)
    byBlock.set(e.electiveBlockId, g)
  }
  nonElective.sort((a, b) => a.subjectCode.localeCompare(b.subjectCode))
  const electiveGroups = Array.from(byBlock.values()).sort((a, b) =>
    a.name.localeCompare(b.name),
  )
  return { nonElective, electiveGroups }
}

// ─── component ────────────────────────────────────────────────────────────────

export function OfficialTimetablePrint({
  type,
  title,
  programCode,
  semester,
  effectiveDate,
  coordinator,
  entries,
  slots,
  pageBreak = false,
}: OfficialTimetablePrintProps) {
  // ── derived data ───────────────────────────────────────────────────────
  const slotIndices = React.useMemo(
    () =>
      Array.from(new Set(slots.map((s) => s.slot_index))).sort((a, b) => a - b),
    [slots],
  )

  const slotMeta = React.useMemo(() => {
    const m = new Map<number, TimeSlot>()
    for (const s of slots) {
      if (!m.has(s.slot_index)) m.set(s.slot_index, s)
    }
    return m
  }, [slots])

  const days = React.useMemo(() => {
    const set = new Set<number>()
    for (const s of slots) set.add(s.day_of_week)
    for (const e of entries) set.add(e.day)
    return Array.from(set).sort((a, b) => a - b)
  }, [slots, entries])

  const cellMap = React.useMemo(() => {
    const map = new Map<string, OfficialEntry[]>()
    for (const e of entries) {
      const key = ck(e.day, e.slotIndex)
      const arr = map.get(key) ?? []
      arr.push(e)
      map.set(key, arr)
    }
    for (const [k, arr] of map.entries()) {
      arr.sort((a, b) => a.subjectCode.localeCompare(b.subjectCode))
      map.set(k, arr)
    }
    return map
  }, [entries])

  // Detect consecutive same-entry blocks (lab merging via colSpan).
  const { colSpanByCell, skipCells } = React.useMemo(() => {
    const colSpanByCell = new Map<string, { entry: OfficialEntry; colSpan: number }>()
    const skipCells = new Set<string>()
    for (const day of days) {
      for (let i = 0; i < slotIndices.length; i++) {
        const si = slotIndices[i]
        const key = ck(day, si)
        if (skipCells.has(key)) continue
        const items = cellMap.get(key) ?? []
        if (items.length !== 1) continue
        const entry = items[0]
        let colSpan = 1
        for (let j = i + 1; j < slotIndices.length; j++) {
          const nextKey = ck(day, slotIndices[j])
          if (skipCells.has(nextKey)) break
          const nextItems = cellMap.get(nextKey) ?? []
          if (nextItems.length !== 1) break
          const next = nextItems[0]
          if (
            next.subjectCode !== entry.subjectCode ||
            next.teacherName !== entry.teacherName ||
            next.roomCode !== entry.roomCode ||
            next.sectionCode !== entry.sectionCode
          )
            break
          colSpan++
          skipCells.add(nextKey)
        }
        if (colSpan > 1) colSpanByCell.set(key, { entry, colSpan })
      }
    }
    return { colSpanByCell, skipCells }
  }, [days, slotIndices, cellMap])

  // Subject legend rows — one per unique subject_code
  const legendRows = React.useMemo(() => {
    const seen = new Map<
      string,
      { code: string; name: string; teacher: string; sections: Set<string> }
    >()
    for (const e of entries) {
      const row = seen.get(e.subjectCode) ?? {
        code: e.subjectCode,
        name: e.subjectName,
        teacher: e.teacherName,
        sections: new Set<string>(),
      }
      if (e.sectionCode) row.sections.add(e.sectionCode)
      seen.set(e.subjectCode, row)
    }
    return Array.from(seen.values()).sort((a, b) => a.code.localeCompare(b.code))
  }, [entries])

  // ── cell line helpers ──────────────────────────────────────────────────
  function cellLines(e: OfficialEntry): [string, string, string] {
    if (type === 'section') return [e.subjectCode, e.roomCode, e.teacherName]
    if (type === 'faculty') return [e.subjectCode, e.roomCode, e.sectionCode]
    // room: show section + teacher
    return [e.subjectCode, e.sectionCode, e.teacherName]
  }

  const headerTypeLabel =
    type === 'section' ? 'Section' : type === 'faculty' ? 'Faculty' : 'Room'

  const legendCol3Label = type === 'section' ? 'Faculty Name' : 'Section(s)'

  const generatedOn = new Date().toLocaleDateString('en-IN', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })

  return (
    <div
      className={pageBreak ? 'opt-page-break' : ''}
      style={{ fontFamily: "'Times New Roman', Times, serif" }}
    >
      {/* ── header ──────────────────────────────────────────────────── */}
      <div
        style={{
          border: '1.5px solid #1e293b',
          padding: '6px 10px',
          marginBottom: 10,
          textAlign: 'center',
        }}
      >
        <div
          style={{
            fontSize: 13,
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
          }}
        >
          {programCode ? `${programCode} — ` : ''}Time Table
        </div>
        <div
          style={{
            marginTop: 4,
            display: 'flex',
            justifyContent: 'center',
            gap: 40,
            fontSize: 11,
          }}
        >
          <span>
            <strong>Semester:</strong> {semester || '—'}
          </span>
          <span>
            <strong>{headerTypeLabel}:</strong> {title}
          </span>
          <span>
            <strong>w.e.f:</strong> {effectiveDate || '—'}
          </span>
        </div>
      </div>

      {/* ── timetable grid ──────────────────────────────────────────── */}
      <div style={{ overflowX: 'auto', marginBottom: 16 }}>
        <table className="opt-table">
          <thead>
            <tr className="opt-header-row">
              <th className="opt-cell-day" style={{ textAlign: 'center' }}>
                Day
              </th>
              {slotIndices.map((si) => {
                const meta = slotMeta.get(si)
                return (
                  <th key={si} style={{ textAlign: 'center', minWidth: 88 }}>
                    {meta
                      ? `${fmtTime(meta.start_time)}–${fmtTime(meta.end_time)}`
                      : `#${si}`}
                  </th>
                )
              })}
            </tr>
          </thead>

          <tbody>
            {days.map((day) => (
              <tr key={day}>
                {/* day label */}
                <td className="opt-cell-day">
                  {DAYS_SHORT[day] ?? `D${day}`}
                </td>

                {slotIndices.map((si) => {
                  const key = ck(day, si)

                  // cell belongs to a merged lab block — skip
                  if (skipCells.has(key)) return null

                  const colInfo = colSpanByCell.get(key)
                  const items = cellMap.get(key) ?? []

                  // empty
                  if (items.length === 0) {
                    return (
                      <td key={si} className="opt-cell opt-cell-empty">
                        —
                      </td>
                    )
                  }

                  // lab block with colSpan
                  if (colInfo) {
                    const [l1, l2, l3] = cellLines(colInfo.entry)
                    return (
                      <td
                        key={si}
                        colSpan={colInfo.colSpan}
                        className="opt-cell opt-cell-lab"
                      >
                        <div className="opt-line1">{l1}</div>
                        <div className="opt-line2">{l2}</div>
                        <div className="opt-line3">{l3}</div>
                      </td>
                    )
                  }

                  // regular / elective cell
                  const { nonElective, electiveGroups } = groupElective(items)
                  return (
                    <td key={si} className="opt-cell">
                      {electiveGroups.map((g, gi) => (
                        <div
                          key={gi}
                          className={gi > 0 ? 'opt-cell-divider' : ''}
                        >
                          <div className="opt-elective-label">{g.name}</div>
                          {g.items.map((e, ei) => {
                            const [l1, l2, l3] = cellLines(e)
                            return (
                              <div
                                key={ei}
                                className={ei > 0 ? 'opt-cell-divider' : ''}
                              >
                                <div className="opt-line1">{l1}</div>
                                <div className="opt-line2">{l2}</div>
                                <div className="opt-line3">{l3}</div>
                              </div>
                            )
                          })}
                        </div>
                      ))}

                      {nonElective.map((e, ei) => {
                        const [l1, l2, l3] = cellLines(e)
                        return (
                          <div
                            key={ei}
                            className={
                              ei > 0 || electiveGroups.length > 0
                                ? 'opt-cell-divider'
                                : ''
                            }
                          >
                            <div className="opt-line1">{l1}</div>
                            <div className="opt-line2">{l2}</div>
                            <div className="opt-line3">{l3}</div>
                          </div>
                        )
                      })}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── subject legend ──────────────────────────────────────────── */}
      {legendRows.length > 0 && (
        <>
          <div className="opt-legend-title">Subject Legend</div>
          <table className="opt-table" style={{ marginBottom: 12 }}>
            <thead>
              <tr className="opt-header-row">
                <th style={{ textAlign: 'left', width: '14%' }}>
                  Subject Code
                </th>
                <th style={{ textAlign: 'left', width: '52%' }}>
                  Subject Name
                </th>
                <th style={{ textAlign: 'left', width: '34%' }}>
                  {legendCol3Label}
                </th>
              </tr>
            </thead>
            <tbody>
              {legendRows.map((row) => (
                <tr key={row.code}>
                  <td style={{ fontWeight: 600 }}>{row.code}</td>
                  <td>{row.name}</td>
                  <td>
                    {type === 'section'
                      ? row.teacher
                      : Array.from(row.sections).sort().join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* ── footer ──────────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 10,
          color: '#64748b',
          borderTop: '1px solid #94a3b8',
          paddingTop: 6,
          fontFamily: "'Times New Roman', Times, serif",
        }}
      >
        {coordinator ? (
          <span>
            <strong>CLASS CO-ORDINATOR:</strong> {coordinator}
          </span>
        ) : (
          <span />
        )}
        <span>Generated: {generatedOn}</span>
      </div>
    </div>
  )
}
