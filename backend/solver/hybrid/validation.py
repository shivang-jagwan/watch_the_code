from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from solver.hybrid.models import (
    ClassDef,
    CurriculumEntry,
    EventRequirement,
    ExclusiveRoomOwnership,
    Institution,
    PreparedProblem,
    ProblemInput,
    Room,
    Slot,
    Subject,
    Teacher,
)


class InputValidationError(ValueError):
    pass


def _parse_slot(raw: dict[str, Any]) -> Slot:
    return (int(raw["day"]), int(raw["period"]))


def _full_slot_domain(inst: Institution) -> set[Slot]:
    lunch = set(inst.lunch_break_periods)
    return {
        (day, period)
        for day in range(inst.days_per_week)
        for period in range(inst.periods_per_day)
        if period not in lunch
    }


def parse_problem_input(data: dict[str, Any]) -> ProblemInput:
    inst_raw = data["institution"]
    lunch_raw = inst_raw.get("lunch_break")
    if lunch_raw is None:
        lunch = ()
    elif isinstance(lunch_raw, int):
        lunch = (int(lunch_raw),)
    else:
        lunch = tuple(sorted({int(v) for v in lunch_raw}))
    institution = Institution(
        days_per_week=int(inst_raw["days_per_week"]),
        periods_per_day=int(inst_raw["periods_per_day"]),
        lunch_break_periods=lunch,
    )

    all_slots = _full_slot_domain(institution)

    rooms: list[Room] = []
    for raw in data.get("rooms", []):
        raw_slots = raw.get("available_periods")
        if raw_slots:
            slots = frozenset(_parse_slot(x) for x in raw_slots)
        else:
            slots = frozenset(all_slots)
        rooms.append(
            Room(
                id=str(raw["id"]),
                capacity=int(raw.get("capacity", 0)),
                room_type=str(raw["type"]),
                available_periods=slots,
            )
        )

    subjects: list[Subject] = []
    for raw in data.get("subjects", []):
        subjects.append(
            Subject(
                id=str(raw["id"]),
                requires_room_type=str(raw["requires_room_type"]),
                min_lectures_per_week=int(raw["min_lectures_per_week"]),
                split_allowed=bool(raw.get("split_allowed", True)),
            )
        )

    teachers: list[Teacher] = []
    for raw in data.get("teachers", []):
        avail_raw = raw.get("availability")
        pref_raw = raw.get("preferences")
        availability = (
            frozenset(_parse_slot(x) for x in avail_raw) if avail_raw else frozenset(all_slots)
        )
        preferences = frozenset(_parse_slot(x) for x in pref_raw) if pref_raw else frozenset()
        teachers.append(
            Teacher(
                id=str(raw["id"]),
                subjects=frozenset(str(v) for v in raw.get("subjects", [])),
                max_lectures_per_week=int(raw["max_lectures_per_week"]),
                availability=availability,
                preferences=preferences,
            )
        )

    classes: list[ClassDef] = []
    for raw in data.get("classes", []):
        curriculum = []
        for item in raw.get("curriculum", []):
            curriculum.append(
                CurriculumEntry(
                    subject_id=str(item["subject_id"]),
                    teacher_id=str(item["teacher_id"]),
                    min_per_week=int(item["min_per_week"]),
                )
            )
        classes.append(ClassDef(id=str(raw["id"]), curriculum=tuple(curriculum)))

    ownership = tuple(
        ExclusiveRoomOwnership(room_id=str(r["room_id"]), subject_id=str(r["subject_id"]))
        for r in data.get("exclusive_room_ownership", [])
    )

    return ProblemInput(
        institution=institution,
        rooms=tuple(rooms),
        subjects=tuple(subjects),
        teachers=tuple(teachers),
        classes=tuple(classes),
        exclusive_room_ownership=ownership,
    )


def load_problem_input(path: str | Path) -> ProblemInput:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_problem_input(data)


def build_event_requirements(problem: ProblemInput) -> tuple[EventRequirement, ...]:
    subject_by_id = {s.id: s for s in problem.subjects}
    events: list[EventRequirement] = []
    eid = 0
    for class_obj in problem.classes:
        for entry in class_obj.curriculum:
            subject = subject_by_id[entry.subject_id]
            required_count = max(subject.min_lectures_per_week, entry.min_per_week)
            for _ in range(required_count):
                events.append(
                    EventRequirement(
                        event_id=eid,
                        class_id=class_obj.id,
                        subject_id=entry.subject_id,
                        teacher_id=entry.teacher_id,
                    )
                )
                eid += 1
    return tuple(events)


def prepare_problem(problem: ProblemInput) -> PreparedProblem:
    validate_problem(problem)
    events = build_event_requirements(problem)

    all_slots = tuple(sorted(_full_slot_domain(problem.institution)))
    room_by_id = {r.id: r for r in problem.rooms}
    subject_by_id = {s.id: s for s in problem.subjects}
    teacher_by_id = {t.id: t for t in problem.teachers}

    room_exclusive_subject: dict[str, str] = {}
    subject_exclusive_room_ids: dict[str, set[str]] = defaultdict(set)
    for rel in problem.exclusive_room_ownership:
        room_exclusive_subject[rel.room_id] = rel.subject_id
        subject_exclusive_room_ids[rel.subject_id].add(rel.room_id)

    allowed_pairs_by_event: dict[int, tuple[tuple[Slot, str], ...]] = {}
    for event in events:
        subject = subject_by_id[event.subject_id]
        teacher = teacher_by_id[event.teacher_id]
        pairs: list[tuple[Slot, str]] = []
        for room in problem.rooms:
            if room.room_type != subject.requires_room_type:
                continue
            locked_subject = room_exclusive_subject.get(room.id)
            if locked_subject is not None and locked_subject != event.subject_id:
                continue
            for slot in all_slots:
                if slot not in teacher.availability:
                    continue
                if slot not in room.available_periods:
                    continue
                pairs.append((slot, room.id))
        if not pairs:
            raise InputValidationError(
                f"No feasible slot-room pair for event {event.event_id} ({event.class_id}, {event.subject_id}, {event.teacher_id})."
            )
        allowed_pairs_by_event[event.event_id] = tuple(sorted(pairs))

    return PreparedProblem(
        problem=problem,
        events=events,
        all_teaching_slots=all_slots,
        room_by_id=room_by_id,
        subject_by_id=subject_by_id,
        teacher_by_id=teacher_by_id,
        allowed_pairs_by_event=allowed_pairs_by_event,
        subject_exclusive_room_ids={k: frozenset(v) for k, v in subject_exclusive_room_ids.items()},
        room_exclusive_subject=room_exclusive_subject,
    )


def validate_problem(problem: ProblemInput) -> None:
    errors: list[str] = []
    inst = problem.institution

    if inst.days_per_week <= 0 or inst.periods_per_day <= 0:
        errors.append("Institution dimensions must be positive.")

    subject_by_id = {s.id: s for s in problem.subjects}
    teacher_by_id = {t.id: t for t in problem.teachers}
    room_types = Counter(r.room_type for r in problem.rooms)

    seen_room_ids = set()
    for room in problem.rooms:
        if room.id in seen_room_ids:
            errors.append(f"Duplicate room id: {room.id}")
        seen_room_ids.add(room.id)

    seen_subject_ids = set()
    for subject in problem.subjects:
        if subject.id in seen_subject_ids:
            errors.append(f"Duplicate subject id: {subject.id}")
        seen_subject_ids.add(subject.id)

    seen_teacher_ids = set()
    for teacher in problem.teachers:
        if teacher.id in seen_teacher_ids:
            errors.append(f"Duplicate teacher id: {teacher.id}")
        seen_teacher_ids.add(teacher.id)

    available_slots_per_week = len(_full_slot_domain(inst))
    total_required = 0
    teacher_required = Counter()

    for class_obj in problem.classes:
        for entry in class_obj.curriculum:
            if entry.subject_id not in subject_by_id:
                errors.append(f"Class {class_obj.id} references unknown subject {entry.subject_id}")
                continue
            if entry.teacher_id not in teacher_by_id:
                errors.append(f"Class {class_obj.id} references unknown teacher {entry.teacher_id}")
                continue

            subject = subject_by_id[entry.subject_id]
            teacher = teacher_by_id[entry.teacher_id]

            if entry.subject_id not in teacher.subjects:
                errors.append(
                    f"Teacher-subject incompatibility: teacher {teacher.id} cannot teach subject {entry.subject_id}"
                )

            required = max(subject.min_lectures_per_week, entry.min_per_week)
            total_required += required
            teacher_required[teacher.id] += required

            matching_rooms = [r for r in problem.rooms if r.room_type == subject.requires_room_type]
            if not matching_rooms:
                errors.append(
                    f"No rooms of type {subject.requires_room_type} available for subject {subject.id}"
                )
            else:
                distinct_room_slots = set()
                for room in matching_rooms:
                    distinct_room_slots.update(room.available_periods)
                if len(distinct_room_slots) < required:
                    errors.append(
                        f"Room availability infeasible for subject {subject.id}: required={required} available_slots={len(distinct_room_slots)}"
                    )

            feasible_teacher_slots = len(teacher.availability)
            if feasible_teacher_slots < required:
                errors.append(
                    f"Teacher {teacher.id} availability ({feasible_teacher_slots}) is lower than required lectures ({required})"
                )

    class_count = len(problem.classes)
    total_capacity = available_slots_per_week * class_count
    if total_required > total_capacity:
        errors.append(
            f"Total required lectures ({total_required}) exceed available class slots ({total_capacity})"
        )

    for teacher in problem.teachers:
        if teacher_required[teacher.id] > teacher.max_lectures_per_week:
            errors.append(
                f"Teacher {teacher.id} workload infeasible: required={teacher_required[teacher.id]} max={teacher.max_lectures_per_week}"
            )

    for rel in problem.exclusive_room_ownership:
        if rel.room_id not in seen_room_ids:
            errors.append(f"Exclusive ownership references unknown room {rel.room_id}")
        if rel.subject_id not in seen_subject_ids:
            errors.append(f"Exclusive ownership references unknown subject {rel.subject_id}")

    for room_type, count in room_types.items():
        if count <= 0:
            errors.append(f"Room type {room_type} has no rooms")

    if errors:
        raise InputValidationError("\n".join(errors))
