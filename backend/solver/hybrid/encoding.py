from __future__ import annotations

from dataclasses import dataclass

from solver.hybrid.models import EventRequirement


@dataclass(frozen=True)
class Gene:
    event_id: int
    class_id: str
    subject_id: str
    teacher_id: str
    room_id: str
    day: int
    period: int

    @property
    def slot(self) -> tuple[int, int]:
        return (self.day, self.period)


@dataclass(frozen=True)
class Chromosome:
    genes: tuple[Gene, ...]

    def by_event(self) -> dict[int, Gene]:
        return {g.event_id: g for g in self.genes}


def chromosome_from_event_assignments(
    events: tuple[EventRequirement, ...],
    assignments: dict[int, tuple[tuple[int, int], str]],
) -> Chromosome:
    genes: list[Gene] = []
    for event in events:
        slot, room_id = assignments[event.event_id]
        genes.append(
            Gene(
                event_id=event.event_id,
                class_id=event.class_id,
                subject_id=event.subject_id,
                teacher_id=event.teacher_id,
                room_id=room_id,
                day=slot[0],
                period=slot[1],
            )
        )
    return Chromosome(genes=tuple(genes))
