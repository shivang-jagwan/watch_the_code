from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from solver.hybrid.encoding import Chromosome
from solver.hybrid.models import EngineResult, PreparedProblem


def _slot_label(day: int, period: int) -> str:
    return f"D{day + 1}-P{period + 1}"


def build_class_grid(chromosome: Chromosome) -> dict[str, dict[str, dict[str, str]]]:
    grid: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    for g in chromosome.genes:
        day_key = f"day_{g.day + 1}"
        period_key = f"period_{g.period + 1}"
        grid[g.class_id][day_key][period_key] = f"{g.subject_id} | {g.teacher_id} | {g.room_id}"
    return {k: dict(v) for k, v in grid.items()}


def build_teacher_grid(chromosome: Chromosome) -> dict[str, dict[str, dict[str, str]]]:
    grid: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    for g in chromosome.genes:
        day_key = f"day_{g.day + 1}"
        period_key = f"period_{g.period + 1}"
        grid[g.teacher_id][day_key][period_key] = f"{g.class_id} | {g.subject_id} | {g.room_id}"
    return {k: dict(v) for k, v in grid.items()}


def build_room_grid(chromosome: Chromosome) -> dict[str, dict[str, dict[str, str]]]:
    grid: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    for g in chromosome.genes:
        day_key = f"day_{g.day + 1}"
        period_key = f"period_{g.period + 1}"
        grid[g.room_id][day_key][period_key] = f"{g.class_id} | {g.subject_id} | {g.teacher_id}"
    return {k: dict(v) for k, v in grid.items()}


def _write_convergence_svg(path: Path, best_values: Iterable[float], mean_values: Iterable[float]) -> None:
    best = list(best_values)
    mean = list(mean_values)
    if not best:
        path.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
        return

    width = 960
    height = 360
    pad = 30
    ymax = max(max(best), max(mean), 1.0)
    xmax = max(len(best) - 1, 1)

    def pt(i: int, v: float) -> tuple[float, float]:
        x = pad + (width - 2 * pad) * (i / xmax)
        y = height - pad - (height - 2 * pad) * (v / ymax)
        return x, y

    best_points = " ".join(f"{pt(i, v)[0]:.2f},{pt(i, v)[1]:.2f}" for i, v in enumerate(best))
    mean_points = " ".join(f"{pt(i, v)[0]:.2f},{pt(i, v)[1]:.2f}" for i, v in enumerate(mean))

    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}'>
<rect width='{width}' height='{height}' fill='#f7fafc'/>
<line x1='{pad}' y1='{height-pad}' x2='{width-pad}' y2='{height-pad}' stroke='#94a3b8' />
<line x1='{pad}' y1='{pad}' x2='{pad}' y2='{height-pad}' stroke='#94a3b8' />
<polyline fill='none' stroke='#0f766e' stroke-width='3' points='{best_points}' />
<polyline fill='none' stroke='#0ea5e9' stroke-width='2' points='{mean_points}' />
<text x='{pad}' y='20' font-family='Segoe UI' font-size='14' fill='#1e293b'>Best fitness</text>
<text x='150' y='20' font-family='Segoe UI' font-size='14' fill='#0369a1'>Mean fitness</text>
</svg>"""
    path.write_text(svg, encoding="utf-8")


def write_outputs(prepared: PreparedProblem, result: EngineResult, output_dir: str | Path) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    chromosome = result.best_chromosome

    class_grid = build_class_grid(chromosome)
    teacher_grid = build_teacher_grid(chromosome)
    room_grid = build_room_grid(chromosome)

    full_json_path = out / "timetable_full.json"
    csv_path = out / "timetable_lectures.csv"
    html_path = out / "timetable.html"
    svg_path = out / "fitness_convergence.svg"

    payload = {
        "fitness": result.best_fitness,
        "breakdown": result.best_breakdown.__dict__,
        "genes": [g.__dict__ for g in chromosome.genes],
        "class_grid": class_grid,
        "teacher_grid": teacher_grid,
        "room_grid": room_grid,
        "history_best": list(result.history_best),
        "history_mean": list(result.history_mean),
    }
    full_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["event_id", "class_id", "subject_id", "teacher_id", "room_id", "day", "period", "slot"],
        )
        writer.writeheader()
        for g in chromosome.genes:
            writer.writerow(
                {
                    "event_id": g.event_id,
                    "class_id": g.class_id,
                    "subject_id": g.subject_id,
                    "teacher_id": g.teacher_id,
                    "room_id": g.room_id,
                    "day": g.day,
                    "period": g.period,
                    "slot": _slot_label(g.day, g.period),
                }
            )

    _write_convergence_svg(svg_path, result.history_best, result.history_mean)

    def render_grid(title: str, grid: dict[str, dict[str, dict[str, str]]]) -> str:
        cards = []
        for owner_id, days in sorted(grid.items()):
            rows = []
            for day, periods in sorted(days.items()):
                row_cells = "".join(
                    f"<li><strong>{period}</strong>: {entry}</li>" for period, entry in sorted(periods.items())
                )
                rows.append(f"<h4>{day}</h4><ul>{row_cells}</ul>")
            cards.append(f"<article><h3>{owner_id}</h3>{''.join(rows)}</article>")
        return f"<section><h2>{title}</h2>{''.join(cards)}</section>"

    html = f"""<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>ChronoGen Hybrid Timetable</title>
  <style>
    :root {{ --bg:#fffaf2; --card:#ffffff; --ink:#1f2937; --line:#cbd5e1; --brand:#0f766e; }}
    body {{ font-family:'Segoe UI',sans-serif; margin:0; padding:24px; color:var(--ink); background:radial-gradient(circle at 10% 10%, #fef3c7, #fffaf2 45%, #e0f2fe); }}
    h1,h2,h3,h4 {{ margin:.2rem 0; }}
    main {{ display:grid; gap:20px; }}
    section {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; }}
    article {{ border-top:1px dashed var(--line); padding-top:10px; margin-top:10px; }}
    ul {{ margin:0; padding-left:20px; }}
    .meta {{ display:flex; gap:16px; flex-wrap:wrap; }}
    .chip {{ padding:8px 10px; border-radius:999px; background:#ecfeff; border:1px solid #99f6e4; }}
    img {{ width:100%; max-width:960px; border:1px solid var(--line); border-radius:10px; background:#f8fafc; }}
  </style>
</head>
<body>
<main>
  <section>
    <h1>ChronoGen Hybrid Timetable</h1>
    <div class='meta'>
      <div class='chip'>Best fitness: {result.best_fitness:.2f}</div>
      <div class='chip'>Generations: {result.generations_ran}</div>
      <div class='chip'>Hard constraints satisfied: {'yes' if result.best_breakdown.teacher_conflicts == 0 and result.best_breakdown.room_conflicts == 0 and result.best_breakdown.class_conflicts == 0 and result.best_breakdown.exclusive_room_violations == 0 else 'no'}</div>
    </div>
    <p>Penalty breakdown: {result.best_breakdown.__dict__}</p>
    <h2>Fitness Convergence</h2>
    <img src='fitness_convergence.svg' alt='Fitness convergence graph'>
  </section>
  {render_grid('Class Timetable Grid', class_grid)}
  {render_grid('Teacher Timetable Grid', teacher_grid)}
  {render_grid('Room Occupancy Grid', room_grid)}
</main>
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8")

    return {
        "json": str(full_json_path),
        "csv": str(csv_path),
        "html": str(html_path),
        "convergence_svg": str(svg_path),
    }
