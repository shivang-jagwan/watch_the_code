from __future__ import annotations

from collections import defaultdict
from math import ceil
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.tenant import where_tenant
from core.db import table_exists
from models import (
    Section,
    Subject,
    Teacher,
    TeacherSubjectSection,
    SectionSubject,
    SectionTimeWindow,
    TimeSlot,
    Room,
    FixedTimetableEntry,
    SpecialAllotment,
    CombinedGroup,
    CombinedGroupSection,
)
from models.curriculum_subject import CurriculumSubject
from models.track_subject import TrackSubject

from models.combined_subject_group import CombinedSubjectGroup
from models.combined_subject_section import CombinedSubjectSection
from models.subject_allowed_room import SubjectAllowedRoom


def _slots_for_subject(subj: Any, sessions_per_week: int) -> int:
    if str(getattr(subj, "subject_type", "THEORY")) == "LAB":
        block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
        if block < 1:
            block = 1
        return int(sessions_per_week) * int(block)
    return int(sessions_per_week)


def build_capacity_data(
    db: Session,
    *,
    program_id: Any,
    academic_year_id: Any | None,
    sections: list[Section],
    tenant_id: Any | None,
) -> dict[str, Any]:
    section_ids = [s.id for s in sections]

    # Subjects by id and mapped per-section curriculum
    subjects: list[Subject] = db.execute(where_tenant(select(Subject), Subject, tenant_id)).scalars().all()
    subject_by_id = {s.id: s for s in subjects}

    mapped_subject_ids_by_section: dict[Any, list[Any]] = defaultdict(list)
    sessions_per_week_by_section_subject: dict[tuple[Any, Any], int] = {}
    lab_block_by_section_subject: dict[tuple[Any, Any], int] = {}
    if section_ids:
        q_sec_subj = select(SectionSubject.section_id, SectionSubject.subject_id).where(
            SectionSubject.section_id.in_(section_ids)
        )
        q_sec_subj = where_tenant(q_sec_subj, SectionSubject, tenant_id)
        for sec_id, subj_id in db.execute(q_sec_subj).all():
            mapped_subject_ids_by_section[sec_id].append(subj_id)

    sections_by_id = {s.id: s for s in sections}

    # Build curriculum lookups for refactored schema; fallback to legacy track_subjects.
    curriculum_by_year_track_subject: dict[tuple[Any, str, Any], Any] = {}
    if table_exists(db, "curriculum_subjects"):
        year_ids = sorted({s.academic_year_id for s in sections if getattr(s, "academic_year_id", None) is not None})
        q_curr = select(CurriculumSubject).where(CurriculumSubject.program_id == program_id)
        if year_ids:
            q_curr = q_curr.where(CurriculumSubject.academic_year_id.in_(year_ids))
        q_curr = where_tenant(q_curr, CurriculumSubject, tenant_id)
        for row in db.execute(q_curr).scalars().all():
            curriculum_by_year_track_subject[(row.academic_year_id, str(row.track), row.subject_id)] = row

        for section in sections:
            sec_id = section.id
            track = str(getattr(section, "track", ""))
            year_id = getattr(section, "academic_year_id", None)

            # If section has no explicit mapping, derive mandatory subjects from curriculum.
            if not mapped_subject_ids_by_section.get(sec_id):
                for (yid, trk, subj_id), row in curriculum_by_year_track_subject.items():
                    if yid != year_id or trk != track or bool(getattr(row, "is_elective", False)):
                        continue
                    mapped_subject_ids_by_section[sec_id].append(subj_id)
                    sessions_per_week_by_section_subject[(sec_id, subj_id)] = int(getattr(row, "sessions_per_week", 0) or 0)
                    lab_block_by_section_subject[(sec_id, subj_id)] = int(getattr(row, "lab_block_size_slots", 1) or 1)

            # Apply curriculum overrides even when section_subjects mapping exists.
            for subj_id in mapped_subject_ids_by_section.get(sec_id, []):
                row = curriculum_by_year_track_subject.get((year_id, track, subj_id))
                if row is None:
                    continue
                sessions_per_week_by_section_subject[(sec_id, subj_id)] = int(getattr(row, "sessions_per_week", 0) or 0)
                lab_block_by_section_subject[(sec_id, subj_id)] = int(getattr(row, "lab_block_size_slots", 1) or 1)
    elif table_exists(db, "track_subjects"):
        year_ids = sorted({s.academic_year_id for s in sections if getattr(s, "academic_year_id", None) is not None})
        q_track = select(TrackSubject).where(TrackSubject.program_id == program_id)
        if year_ids:
            q_track = q_track.where(TrackSubject.academic_year_id.in_(year_ids))
        q_track = where_tenant(q_track, TrackSubject, tenant_id)
        track_rows = db.execute(q_track).scalars().all()
        track_by_year_track: dict[tuple[Any, str], list[TrackSubject]] = defaultdict(list)
        for row in track_rows:
            track_by_year_track[(row.academic_year_id, str(row.track))].append(row)

        for section in sections:
            sec_id = section.id
            year_id = getattr(section, "academic_year_id", None)
            track = str(getattr(section, "track", ""))
            rows = track_by_year_track.get((year_id, track), [])

            if not mapped_subject_ids_by_section.get(sec_id):
                for row in rows:
                    if bool(getattr(row, "is_elective", False)):
                        continue
                    mapped_subject_ids_by_section[sec_id].append(row.subject_id)
                    subj = subject_by_id.get(row.subject_id)
                    fallback_sessions = int(getattr(subj, "sessions_per_week", 0) or 0)
                    sessions_per_week_by_section_subject[(sec_id, row.subject_id)] = int(
                        getattr(row, "sessions_override", None) if getattr(row, "sessions_override", None) is not None else fallback_sessions
                    )
                    lab_block_by_section_subject[(sec_id, row.subject_id)] = int(getattr(subj, "lab_block_size_slots", 1) or 1)

    # Assigned teacher per (section, subject)
    assigned_teacher_by_section_subject: dict[tuple[Any, Any], Any] = {}
    if section_ids:
        q_tss = select(TeacherSubjectSection.section_id, TeacherSubjectSection.subject_id, TeacherSubjectSection.teacher_id).where(
            TeacherSubjectSection.section_id.in_(section_ids)
        ).where(TeacherSubjectSection.is_active.is_(True))
        q_tss = where_tenant(q_tss, TeacherSubjectSection, tenant_id)
        for sid, subj_id, tid in db.execute(q_tss).all():
            assigned_teacher_by_section_subject[(sid, subj_id)] = tid

    # Teachers by id
    teachers: list[Teacher] = db.execute(where_tenant(select(Teacher), Teacher, tenant_id)).scalars().all()
    teacher_by_id = {t.id: t for t in teachers}

    # Rooms and room types
    rooms: list[Room] = db.execute(where_tenant(select(Room), Room, tenant_id)).scalars().all()
    rooms_by_type: dict[str, list[Room]] = defaultdict(list)
    for r in rooms:
        t = str(getattr(r, "room_type", "CLASSROOM") or "CLASSROOM").upper()
        rooms_by_type[t].append(r)

    # Time slots and windows
    slots = db.execute(where_tenant(select(TimeSlot), TimeSlot, tenant_id)).scalars().all()
    active_days = sorted({int(getattr(s, "day_of_week")) for s in slots})
    slot_by_day_index: dict[tuple[int, int], Any] = {
        (int(getattr(s, "day_of_week")), int(getattr(s, "slot_index"))): s.id for s in slots
    }
    slot_info: dict[Any, tuple[int, int]] = {
        s.id: (int(getattr(s, "day_of_week")), int(getattr(s, "slot_index"))) for s in slots
    }

    windows = []
    if section_ids:
        q_windows = select(SectionTimeWindow).where(SectionTimeWindow.section_id.in_(section_ids))
        q_windows = where_tenant(q_windows, SectionTimeWindow, tenant_id)
        windows = db.execute(q_windows).scalars().all()

    # Fixed entries and special allotments
    fixed_entries: list[FixedTimetableEntry] = db.execute(where_tenant(select(FixedTimetableEntry), FixedTimetableEntry, tenant_id)).scalars().all()
    special_allotments: list[SpecialAllotment] = db.execute(where_tenant(select(SpecialAllotment), SpecialAllotment, tenant_id)).scalars().all()

    # Combined groups (v2 + legacy fallback)
    group_sections: dict[Any, list[Any]] = defaultdict(list)
    group_subject: dict[Any, Any] = {}
    if section_ids:
        use_v2 = table_exists(db, "combined_groups") and table_exists(db, "combined_group_sections")
        if use_v2:
            q_cg = (
                select(CombinedGroup.id, CombinedGroup.subject_id, CombinedGroupSection.section_id)
                .join(CombinedGroupSection, CombinedGroupSection.combined_group_id == CombinedGroup.id)
                .where(CombinedGroupSection.section_id.in_(section_ids))
            )
            if academic_year_id is not None:
                q_cg = q_cg.where(CombinedGroup.academic_year_id == academic_year_id)
            q_cg = where_tenant(q_cg, CombinedGroup, tenant_id)
            q_cg = where_tenant(q_cg, CombinedGroupSection, tenant_id)
            rows = db.execute(q_cg).all()
        else:
            q_cg = (
                select(CombinedSubjectGroup.id, CombinedSubjectGroup.subject_id, CombinedSubjectSection.section_id)
                .join(CombinedSubjectSection, CombinedSubjectSection.combined_group_id == CombinedSubjectGroup.id)
                .where(CombinedSubjectSection.section_id.in_(section_ids))
            )
            if academic_year_id is not None:
                q_cg = q_cg.where(CombinedSubjectGroup.academic_year_id == academic_year_id)
            q_cg = where_tenant(q_cg, CombinedSubjectGroup, tenant_id)
            q_cg = where_tenant(q_cg, CombinedSubjectSection, tenant_id)
            rows = db.execute(q_cg).all()

        for gid, subj_id, sec_id in rows:
            group_subject[gid] = subj_id
            group_sections[gid].append(sec_id)

    # Filter combined groups to the target `sections` to avoid cross-year/program leakage.
    # Keep only groups with at least 2 relevant sections within the provided section set.
    section_id_set = set(section_ids)
    filtered_group_sections: dict[Any, list[Any]] = {}
    filtered_group_subject: dict[Any, Any] = {}
    for gid, sec_ids in group_sections.items():
        relevant = [sid for sid in sec_ids if sid in section_id_set]
        if len(relevant) >= 2:
            filtered_group_sections[gid] = relevant
            filtered_group_subject[gid] = group_subject.get(gid)

    # Subject-specific allowed rooms (optional table — graceful no-op if missing)
    allowed_rooms_by_subject: dict[Any, list[Any]] = defaultdict(list)
    if table_exists(db, "subject_allowed_rooms"):
        q_sar = where_tenant(
            select(SubjectAllowedRoom.subject_id, SubjectAllowedRoom.room_id),
            SubjectAllowedRoom,
            tenant_id,
        )
        for subj_id, room_id in db.execute(q_sar).all():
            allowed_rooms_by_subject[subj_id].append(room_id)

    return {
        "sections": sections,
        "sections_by_id": sections_by_id,
        "subjects_by_id": subject_by_id,
        "teachers_by_id": teacher_by_id,
        "assigned_teacher_by_section_subject": assigned_teacher_by_section_subject,
        "rooms_by_type": rooms_by_type,
        "rooms": rooms,
        "slots": slots,
        "slot_by_day_index": slot_by_day_index,
        "slot_info": slot_info,
        "windows": windows,
        "fixed_entries": fixed_entries,
        "special_allotments": special_allotments,
        "group_sections": filtered_group_sections,
        "group_subject": filtered_group_subject,
        "active_days": active_days,
        "mapped_subject_ids_by_section": mapped_subject_ids_by_section,
        "sessions_per_week_by_section_subject": sessions_per_week_by_section_subject,
        "lab_block_by_section_subject": lab_block_by_section_subject,
        "allowed_rooms_by_subject": dict(allowed_rooms_by_subject),
    }


def analyze_capacity(data: dict[str, Any], debug: bool = False) -> dict[str, Any]:
    sections: list[Any] = list(data.get("sections") or [])
    section_by_id = {s.id: s for s in sections if getattr(s, "id", None) is not None}
    subject_by_id: dict[Any, Any] = dict(data.get("subjects_by_id") or {})
    teacher_by_id: dict[Any, Any] = dict(data.get("teachers_by_id") or {})
    rooms_by_type = data.get("rooms_by_type") or {}
    rooms_all: list[Any] = list(data.get("rooms") or [])
    room_by_id: dict[Any, Any] = {r.id: r for r in rooms_all}
    allowed_rooms_by_subject: dict[Any, list[Any]] = dict(data.get("allowed_rooms_by_subject") or {})
    slot_info: dict[Any, tuple[int, int]] = dict(data.get("slot_info") or {})
    slot_by_day_index: dict[tuple[int, int], Any] = dict(data.get("slot_by_day_index") or {})
    active_days: list[int] = list(data.get("active_days") or [])
    mapped_subject_ids_by_section: dict[Any, list[Any]] = dict(data.get("mapped_subject_ids_by_section") or {})
    sessions_per_week_by_section_subject: dict[tuple[Any, Any], int] = dict(
        data.get("sessions_per_week_by_section_subject") or {}
    )
    lab_block_by_section_subject: dict[tuple[Any, Any], int] = dict(data.get("lab_block_by_section_subject") or {})
    assigned_teacher_by_section_subject: dict[tuple[Any, Any], Any] = dict(data.get("assigned_teacher_by_section_subject") or {})
    fixed_entries: list[Any] = list(data.get("fixed_entries") or [])
    special_allotments: list[Any] = list(data.get("special_allotments") or [])
    windows: list[Any] = list(data.get("windows") or [])
    group_sections: dict[Any, list[Any]] = dict(data.get("group_sections") or {})
    group_subject: dict[Any, Any] = dict(data.get("group_subject") or {})

    def _slots_for_pair(sec_id: Any, subj_id: Any, subj: Any) -> int:
        sessions = int(
            sessions_per_week_by_section_subject.get(
                (sec_id, subj_id),
                getattr(subj, "sessions_per_week", 0) or 0,
            )
            or 0
        )
        if str(getattr(subj, "subject_type", "THEORY")) == "LAB":
            block = int(
                lab_block_by_section_subject.get(
                    (sec_id, subj_id),
                    getattr(subj, "lab_block_size_slots", 1) or 1,
                )
                or 1
            )
            if block < 1:
                block = 1
            return sessions * block
        return sessions

    # Build window slot sets per section and lock counts per day
    window_slot_ids_by_section: dict[Any, set[Any]] = defaultdict(set)
    locked_slot_indices_by_section_day: dict[tuple[Any, int], set[int]] = defaultdict(set)

    for w in windows:
        sec_id = getattr(w, "section_id", None)
        if sec_id is None:
            continue
        day = int(getattr(w, "day_of_week", 0))
        start = int(getattr(w, "start_slot_index", 0))
        end = int(getattr(w, "end_slot_index", -1))
        for si in range(start, end + 1):
            ts = slot_by_day_index.get((day, si))
            if ts is not None:
                window_slot_ids_by_section[sec_id].add(ts)

    def _lock_slot(sec_id: Any, slot_id: Any) -> None:
        di = slot_info.get(slot_id)
        if not di:
            return
        day, slot_idx = int(di[0]), int(di[1])
        locked_slot_indices_by_section_day[(sec_id, day)].add(slot_idx)

    for fe in fixed_entries:
        _lock_slot(getattr(fe, "section_id", None), getattr(fe, "slot_id", None))
    for sa in special_allotments:
        _lock_slot(getattr(sa, "section_id", None), getattr(sa, "slot_id", None))

    # 1) Required weekly slots per Subject/Section and Combined Groups
    required_by_subject: dict[Any, int] = defaultdict(int)
    required_by_section: dict[Any, int] = defaultdict(int)
    required_by_room_type: dict[str, int] = defaultdict(int)

    # Combined group demand per section still consumes section capacity; track shared teacher separately.
    for sec_id, subj_ids in mapped_subject_ids_by_section.items():
        for subj_id in subj_ids or []:
            subj = subject_by_id.get(subj_id)
            if subj is None:
                continue
            slots_needed = int(_slots_for_pair(sec_id, subj_id, subj))
            if slots_needed <= 0:
                continue
            required_by_subject[subj_id] += int(slots_needed)
            required_by_section[sec_id] += int(slots_needed)
            rt = "LAB" if str(getattr(subj, "subject_type", "THEORY")) == "LAB" else "THEORY"
            if rt == "LAB":
                required_by_room_type["LAB"] += int(slots_needed)
            else:
                required_by_room_type["THEORY"] += int(slots_needed)

    # Combined shared teacher weekly demand (count once per group)
    required_by_teacher: dict[Any, int] = defaultdict(int)
    teacher_contrib: dict[Any, list[dict[str, Any]]] = defaultdict(list)

    counted_combined_groups: set[Any] = set()
    for gid, sec_ids in group_sections.items():
        subj_id = group_subject.get(gid)
        subj = subject_by_id.get(subj_id)
        if subj is None or str(getattr(subj, "subject_type", "THEORY")) != "THEORY":
            continue
        if gid in counted_combined_groups:
            continue
        # Determine single assigned teacher across the group
        assigned_tid = None
        for sid in sec_ids:
            tid = assigned_teacher_by_section_subject.get((sid, subj_id))
            if tid is None:
                assigned_tid = None
                break
            if assigned_tid is None:
                assigned_tid = tid
            elif assigned_tid != tid:
                assigned_tid = None
        if assigned_tid is None:
            continue
        # For combined groups, per-section overrides should be aligned; use first section.
        ref_sid = sec_ids[0] if sec_ids else None
        slots_needed = int(_slots_for_pair(ref_sid, subj_id, subj)) if ref_sid is not None else 0
        if slots_needed <= 0:
            continue
        required_by_teacher[assigned_tid] += int(slots_needed)
        teacher_contrib[assigned_tid].append(
            {
                "source": "COMBINED_GROUP",
                "group_id": str(gid),
                "subject_code": getattr(subj, "code", None),
                "sections": [getattr(section_by_id.get(sid), "code", str(sid)) for sid in sec_ids],
                "slots": int(slots_needed),
            }
        )
        counted_combined_groups.add(gid)

    # Per-section subjects (excluding combined theory counted above)
    for sec_id, subj_ids in mapped_subject_ids_by_section.items():
        for subj_id in subj_ids or []:
            subj = subject_by_id.get(subj_id)
            if subj is None:
                continue
            # Skip combined THEORY to avoid double-counting
            is_combined_member = False
            if str(getattr(subj, "subject_type", "THEORY")) == "THEORY":
                for gid, g_subj in group_subject.items():
                    if g_subj == subj_id and sec_id in group_sections.get(gid, []):
                        is_combined_member = True
                        break
            if is_combined_member:
                continue
            tid = assigned_teacher_by_section_subject.get((sec_id, subj_id))
            if tid is None:
                continue
            slots_needed = int(_slots_for_pair(sec_id, subj_id, subj))
            if slots_needed <= 0:
                continue
            required_by_teacher[tid] += int(slots_needed)
            teacher_contrib[tid].append(
                {
                    "source": "SECTION_SUBJECT",
                    "section_code": getattr(section_by_id.get(sec_id), "code", None),
                    "subject_code": getattr(subj, "code", None),
                    "subject_type": str(getattr(subj, "subject_type", "")),
                    "slots": int(slots_needed),
                }
            )

    # 2) Available capacity per Teacher/Room type/Section
    available_by_teacher: dict[Any, int] = {}
    for tid, teacher in teacher_by_id.items():
        max_per_day = int(getattr(teacher, "max_per_day", 0) or 0)
        off = getattr(teacher, "weekly_off_day", None)
        available_days = [d for d in active_days if off is None or int(off) != int(d)]
        available_by_teacher[tid] = int(max_per_day) * int(len(available_days))

    # Room capacity per week (normal rooms: CLASSROOM + LT)
    theory_room_capacity = (len(rooms_by_type.get("CLASSROOM", []) or []) + len(rooms_by_type.get("LT", []) or [])) * len(active_days) * len({i for d, i in slot_by_day_index.keys() if d in active_days})
    lab_room_capacity = len(rooms_by_type.get("LAB", []) or []) * len(active_days) * len({i for d, i in slot_by_day_index.keys() if d in active_days})
    available_by_room_type = {"THEORY": int(theory_room_capacity), "LAB": int(lab_room_capacity)}

    # Section domain size: free slots in windows minus locks
    available_by_section: dict[Any, int] = {}
    for sec_id, win_ids in window_slot_ids_by_section.items():
        free = set(win_ids)
        for day in active_days:
            locked_indices = locked_slot_indices_by_section_day.get((sec_id, int(day)), set())
            for idx in locked_indices:
                ts = slot_by_day_index.get((int(day), int(idx)))
                if ts is not None:
                    free.discard(ts)
        available_by_section[sec_id] = len(free)

    # 3) Combined group intersection domain size
    group_domain_size: dict[Any, int] = {}
    for gid, sec_ids in group_sections.items():
        intersection: set[Any] | None = None
        for sid in sec_ids:
            free = set(window_slot_ids_by_section.get(sid, set()))
            for day in active_days:
                locked_indices = locked_slot_indices_by_section_day.get((sid, int(day)), set())
                for idx in locked_indices:
                    ts = slot_by_day_index.get((int(day), int(idx)))
                    if ts is not None:
                        free.discard(ts)
            intersection = free if intersection is None else (intersection & free)
        group_domain_size[gid] = len(intersection or set())

    # Issues
    issues: list[dict[str, Any]] = []

    # Teacher overloads
    for tid, req in sorted(required_by_teacher.items(), key=lambda kv: str(kv[0])):
        avail = int(available_by_teacher.get(tid, 0) or 0)
        if int(req) > int(avail):
            t = teacher_by_id.get(tid)
            issues.append(
                {
                    "type": "CAPACITY_OVERLOAD",
                    "resource": f"Teacher {getattr(t, 'code', tid)}",
                    "resource_type": "TEACHER",
                    "teacher_id": str(tid),
                    "required_slots": int(req),
                    "available_slots": int(avail),
                    "shortage": int(req) - int(avail),
                    "contributors": teacher_contrib.get(tid, []),
                }
            )

    # Room scarcity
    for rt, req in sorted(required_by_room_type.items(), key=lambda kv: kv[0]):
        avail = int(available_by_room_type.get(rt, 0) or 0)
        if int(req) > int(avail):
            issues.append(
                {
                    "type": "ROOM_SCARCITY",
                    "resource": f"RoomType {rt}",
                    "resource_type": "ROOM_TYPE",
                    "required_slots": int(req),
                    "available_slots": int(avail),
                    "shortage": int(req) - int(avail),
                }
            )

    # Section slot deficits
    for sec_id, req in sorted(required_by_section.items(), key=lambda kv: str(kv[0])):
        avail = int(available_by_section.get(sec_id, 0) or 0)
        if int(req) > int(avail):
            sec = section_by_id.get(sec_id)
            issues.append(
                {
                    "type": "SECTION_SLOT_DEFICIT",
                    "resource": f"Section {getattr(sec, 'code', sec_id)}",
                    "resource_type": "SECTION",
                    "section_id": str(sec_id),
                    "required_slots": int(req),
                    "available_slots": int(avail),
                    "shortage": int(req) - int(avail),
                }
            )

    # Combined domain collapse
    for gid, domain_size in group_domain_size.items():
        subj_id = group_subject.get(gid)
        subj = subject_by_id.get(subj_id)
        spw = int(getattr(subj, "sessions_per_week", 0) or 0) if subj is not None else 0
        if spw > int(domain_size):
            issues.append(
                {
                    "type": "COMBINED_DOMAIN_COLLAPSE",
                    "resource": f"CombinedGroup {getattr(subj, 'code', subj_id)}",
                    "resource_type": "COMBINED_GROUP",
                    "group_id": str(gid),
                    "subject_id": str(subj_id) if subj_id is not None else None,
                    "required_slots": int(spw),
                    "available_slots": int(domain_size),
                    "shortage": int(spw) - int(domain_size),
                }
            )

    # Subject-specific room capacity (if allowed rooms are configured)
    section_id_set = set(s.id for s in sections)
    total_slots_per_week = len(data.get("slots") or [])
    for subj_id, allowed_room_id_list in sorted(allowed_rooms_by_subject.items(), key=lambda kv: str(kv[0])):
        if not allowed_room_id_list:
            continue
        subj = subject_by_id.get(subj_id)
        if subj is None:
            continue
        spw = int(getattr(subj, "sessions_per_week", 0) or 0)
        if spw <= 0:
            continue
        slots_per_session = _slots_for_subject(subj, 1)  # lab block multiplier

        # Determine total sessions required:
        # - Each independent section with this subject = spw sessions
        # - Each combined group counts once (shared class)
        combined_gids_for_subj = [gid for gid, g_subj in group_subject.items() if g_subj == subj_id]
        sections_in_combined: set[Any] = set()
        combined_count = 0
        for gid in combined_gids_for_subj:
            relevant_secs = [sid for sid in group_sections.get(gid, []) if sid in section_id_set]
            if len(relevant_secs) >= 2:
                sections_in_combined.update(relevant_secs)
                combined_count += 1

        sections_with_subj = [
            sec_id for sec_id, subj_ids in mapped_subject_ids_by_section.items()
            if subj_id in (subj_ids or []) and sec_id in section_id_set
        ]
        individual_count = len([sid for sid in sections_with_subj if sid not in sections_in_combined])
        total_sessions = (combined_count + individual_count) * spw
        total_slots_needed = total_sessions * slots_per_session

        # Allowed rooms capacity
        valid_room_ids = [rid for rid in allowed_room_id_list if room_by_id.get(rid) is not None]
        allowed_room_capacity = len(valid_room_ids) * int(total_slots_per_week)

        if total_slots_needed > allowed_room_capacity:
            issues.append(
                {
                    "type": "SUBJECT_ROOM_RESTRICTION_CONFLICT",
                    "resource": f"Subject {getattr(subj, 'code', subj_id)}",
                    "resource_type": "SUBJECT_ROOM",
                    "subject_id": str(subj_id),
                    "subject_code": getattr(subj, "code", None),
                    "allowed_rooms": int(len(valid_room_ids)),
                    "required_slots": int(total_slots_needed),
                    "available_slots": int(allowed_room_capacity),
                    "shortage": int(total_slots_needed) - int(allowed_room_capacity),
                }
            )

    # Minimal relaxation suggestions (daily bound)
    minimal_relaxation: list[dict[str, Any]] = []
    for tid, req in sorted(required_by_teacher.items(), key=lambda kv: str(kv[0])):
        teacher = teacher_by_id.get(tid)
        if teacher is None:
            continue
        off = getattr(teacher, "weekly_off_day", None)
        available_days = [d for d in active_days if off is None or int(off) != int(d)]
        if not available_days:
            continue
        max_per_day = int(getattr(teacher, "max_per_day", 0) or 0)
        cap = int(max_per_day) * len(available_days)
        if int(req) > int(cap):
            needed_mpd = ceil(int(req) / int(len(available_days)))
            minimal_relaxation.append(
                {
                    "teacher": getattr(teacher, "code", str(tid)),
                    "teacher_id": str(tid),
                    "increase_max_per_day_from": int(max_per_day),
                    "to": int(needed_mpd),
                }
            )

    summary = {
        "required_by_subject": {str(k): int(v) for k, v in required_by_subject.items()},
        "required_by_teacher": {str(k): int(v) for k, v in required_by_teacher.items()},
        "required_by_section": {str(k): int(v) for k, v in required_by_section.items()},
        "required_by_room_type": {k: int(v) for k, v in required_by_room_type.items()},
        "available_by_teacher": {str(k): int(v) for k, v in available_by_teacher.items()},
        "available_by_room_type": {k: int(v) for k, v in available_by_room_type.items()},
        "available_by_section": {str(k): int(v) for k, v in available_by_section.items()},
        "group_domain_size": {str(k): int(v) for k, v in group_domain_size.items()},
    }

    return {
        "issues": issues,
        "summary": summary,
        "minimal_relaxation": minimal_relaxation,
        "debug": debug,
    }
