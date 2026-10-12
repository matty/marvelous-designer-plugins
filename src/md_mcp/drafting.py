"""Pattern pieces drafted from named edges, checked before MD sees them.

MD's call API takes a piece as a flat list of ``(x, y, type)`` points and hands back an
index, and every seam afterwards names an edge by a *line index* that no call ever
returns -- it has to be derived. This module turns a piece described edge by edge into
that point list and the line map that goes with it, and refuses what MD would accept and
then build wrong. Plain Python, never touches MD; ``bridge`` runs what it plans.

Measured on 2026.0.315 (``docs/md-conventions.md``, "Drafting a piece" and "The four
index spaces"), and what each one becomes here:

* **A line runs from one straight point to the next**, absorbing every curve point
  between them, and line ``i`` starts at straight point ``i``. So a piece is drafted as
  corners, each starting a named edge, with the curve points of that edge listed under
  it; edge ``i`` *is* line ``i``. A point that two edges meet at is always a corner,
  which is what a point sewn to two different counterparts has to be.
* **The contour closes itself.** Repeating the first point as the last makes lines that
  cannot be addressed, so it is refused.
* **Type 2 is the curve point to use**; type 1 is undocumented and type 3 has no way to
  set its handles. Only 0 and 2 are ever sent.
* **MD measures a curve as a curve.** A chord-length-parameterized natural cubic spline
  through an edge's points reproduced MD's recorded lengths to better than 0.01 mm, so
  that is the length predicted here, and MD's own figure is compared with it after.
* **Coordinates are absolute and come back as sent**, so a new piece is laid out on the
  2D board by offsetting its own points, clear of every piece already there.
"""

from __future__ import annotations

import math
import re
from typing import Any

#: Edge and piece references are written ``piece.edge``; an edge name has to survive
#: that, and a bare number already means a line index.
EDGE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Point types MD's CreatePatternWithPoints takes, of the ones measured to be usable.
CORNER = 0
CURVE = 2

#: Samples per spline span when measuring a curve or screening it for self-crossing.
SAMPLES_PER_SPAN = 32

#: Space left between a new piece and the pieces already on the 2D board.
DEFAULT_GAP_MM = 50.0

#: How closely MD's read-back corners and straight edges must match what was sent.
POSITION_TOLERANCE_MM = 0.05
#: MD against the spline prediction for a curved edge. The prediction matched to
#: 0.01 mm when measured; this leaves room for curves unlike the measured one.
CURVE_TOLERANCE_MM = 0.5
CURVE_TOLERANCE_RATIO = 0.002


class DraftError(ValueError):
    """The piece cannot be drafted as described, and the message says why."""


Point = tuple[float, float]


def _point(value: Any, where: str) -> Point:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
        or not all(math.isfinite(v) for v in value)
    ):
        raise DraftError(f"{where} must be [x, y] in millimetres, not {value!r}")
    return float(value[0]), float(value[1])


# --- curves ---------------------------------------------------------------------------


def _natural_cubic(ts: list[float], vs: list[float]) -> list[tuple[float, float, float, float]]:
    """Per-span coefficients (a, b, c, d) of a natural cubic spline through (ts, vs)."""
    n = len(ts) - 1
    h = [ts[i + 1] - ts[i] for i in range(n)]
    if n == 1:
        return [(vs[0], (vs[1] - vs[0]) / h[0], 0.0, 0.0)]
    # Tridiagonal system for the second derivatives, natural ends (M0 = Mn = 0).
    lower = [h[i - 1] for i in range(1, n)]
    diag = [2.0 * (h[i - 1] + h[i]) for i in range(1, n)]
    upper = [h[i] for i in range(1, n)]
    rhs = [
        6.0 * ((vs[i + 1] - vs[i]) / h[i] - (vs[i] - vs[i - 1]) / h[i - 1])
        for i in range(1, n)
    ]
    for i in range(1, len(diag)):
        factor = lower[i] / diag[i - 1]
        diag[i] -= factor * upper[i - 1]
        rhs[i] -= factor * rhs[i - 1]
    inner = [0.0] * len(diag)
    for i in range(len(diag) - 1, -1, -1):
        following = upper[i] * inner[i + 1] if i + 1 < len(diag) else 0.0
        inner[i] = (rhs[i] - following) / diag[i]
    m = [0.0, *inner, 0.0]
    spans = []
    for i in range(n):
        a = vs[i]
        b = (vs[i + 1] - vs[i]) / h[i] - h[i] * (2.0 * m[i] + m[i + 1]) / 6.0
        c = m[i] / 2.0
        d = (m[i + 1] - m[i]) / (6.0 * h[i])
        spans.append((a, b, c, d))
    return spans


def edge_samples(points: list[Point], per_span: int = SAMPLES_PER_SPAN) -> list[Point]:
    """Points along an edge, start to end: the polyline itself if it is straight."""
    if len(points) == 2:
        return list(points)
    ts = [0.0]
    for a, b in zip(points, points[1:]):
        ts.append(ts[-1] + math.dist(a, b))
    xs = _natural_cubic(ts, [p[0] for p in points])
    ys = _natural_cubic(ts, [p[1] for p in points])
    samples = [points[0]]
    for i in range(len(points) - 1):
        span = ts[i + 1] - ts[i]
        for k in range(1, per_span + 1):
            u = span * k / per_span
            ax, bx, cx, dx = xs[i]
            ay, by, cy, dy = ys[i]
            samples.append(
                (ax + u * (bx + u * (cx + u * dx)), ay + u * (by + u * (cy + u * dy)))
            )
    return samples


def edge_length(points: list[Point]) -> float:
    """The predicted length of an edge: straight, or the spline MD draws through it."""
    samples = edge_samples(points, per_span=256)
    return sum(math.dist(a, b) for a, b in zip(samples, samples[1:]))


# --- geometry checks ------------------------------------------------------------------


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1, d2 = _cross(q1, q2, p1), _cross(q1, q2, p2)
    d3, d4 = _cross(p1, p2, q1), _cross(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and 0 not in (d1, d2, d3, d4):
        return True

    def on(a: Point, b: Point, c: Point, d: float) -> bool:
        return d == 0 and min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(
            a[1], b[1]) <= c[1] <= max(a[1], b[1])

    return on(q1, q2, p1, d1) or on(q1, q2, p2, d2) or on(p1, p2, q1, d3) or on(p1, p2, q2, d4)


def self_crossings(edges: list[list[Point]]) -> list[tuple[int, int]]:
    """Pairs of edges whose drawn curves cross or touch away from a shared corner."""
    # A coarser sampling than the length's: this is a screen, and it is O(n^2).
    sampled = [edge_samples(points, per_span=8) for points in edges]
    segments = [
        (edge, a, b)
        for edge, samples in enumerate(sampled)
        for a, b in zip(samples, samples[1:])
    ]
    total = len(segments)
    found: set[tuple[int, int]] = set()
    for i in range(total):
        for j in range(i + 1, total):
            if j == i + 1 or (i == 0 and j == total - 1):
                continue  # consecutive segments share an end point by construction
            (ei, a1, a2), (ej, b1, b2) = segments[i], segments[j]
            if _segments_cross(a1, a2, b1, b2):
                found.add((min(ei, ej), max(ei, ej)))
    return sorted(found)


def signed_area(outline: list[Point]) -> float:
    return 0.5 * sum(
        a[0] * b[1] - b[0] * a[1] for a, b in zip(outline, outline[1:] + outline[:1])
    )


# --- the plan -------------------------------------------------------------------------


def plan(name: str, outline: list[dict]) -> dict:
    """Validate a piece described edge by edge, and return what MD should be sent.

    ``outline`` is the piece's corners in order, each ``{"at": [x, y], "edge": name}``
    with optional ``"through": [[x, y], ...]``: the curve points of the edge that runs
    from this corner to the next. The last edge runs back to the first corner.

    Returns ``{name, points, edges, bbox, area_mm2, winding}``, where ``points`` is the
    list for CreatePatternWithPoints and each edge carries its line index, end corners
    and predicted length.
    """
    if not isinstance(name, str) or not name.strip():
        raise DraftError("a piece needs a name: it is how every later call finds it")
    if not isinstance(outline, list) or len(outline) < 2:
        raise DraftError("an outline needs at least two corners")

    corners: list[Point] = []
    throughs: list[list[Point]] = []
    names: list[str] = []
    for number, corner in enumerate(outline):
        if not isinstance(corner, dict):
            raise DraftError(f"corner {number} must be an object with 'at' and 'edge'")
        unknown = set(corner) - {"at", "edge", "through"}
        if unknown:
            raise DraftError(f"corner {number} has unknown keys {sorted(unknown)}")
        corners.append(_point(corner.get("at"), f"corner {number} 'at'"))
        edge = corner.get("edge")
        if not isinstance(edge, str) or not EDGE_NAME.match(edge):
            raise DraftError(
                f"corner {number} needs an 'edge' name made of letters, digits and _ "
                f"(not starting with a digit), not {edge!r}: it is how md_sew finds the "
                "edge that starts there"
            )
        if edge in names:
            raise DraftError(f"edge name {edge!r} is used twice")
        names.append(edge)
        through = corner.get("through") or []
        if not isinstance(through, list):
            raise DraftError(f"corner {number} 'through' must be a list of [x, y]")
        throughs.append(
            [_point(p, f"corner {number} through[{k}]") for k, p in enumerate(through)]
        )

    if len(corners) > 2 and math.dist(corners[0], corners[-1]) < POSITION_TOLERANCE_MM:
        raise DraftError(
            "the last corner repeats the first. MD closes the contour itself, and a "
            "repeated point makes lines that cannot be addressed -- leave it off"
        )
    if len(corners) == 2 and not any(throughs):
        raise DraftError("two corners and no curve points is a line, not a piece")

    edges_points: list[list[Point]] = []
    for i, start in enumerate(corners):
        end = corners[(i + 1) % len(corners)]
        points = [start, *throughs[i], end]
        for a, b in zip(points, points[1:]):
            if math.dist(a, b) < POSITION_TOLERANCE_MM:
                raise DraftError(
                    f"edge {names[i]!r} has two points in the same place {a}; MD "
                    "refuses a degenerate outline without saying so"
                )
        edges_points.append(points)

    crossings = self_crossings(edges_points)
    if crossings:
        described = ", ".join(f"{names[a]} and {names[b]}" for a, b in crossings)
        raise DraftError(
            f"the outline crosses itself ({described}). MD accepts it and then fails "
            "to mesh it, with the error in its own window rather than the reply"
        )

    polygon = [p for points in edges_points for p in points[:-1]]
    area = signed_area(polygon)
    if abs(area) < 1.0:
        raise DraftError("the outline encloses no area")

    md_points: list[tuple[float, float, int]] = []
    edges: list[dict] = []
    for i, points in enumerate(edges_points):
        md_points.append((points[0][0], points[0][1], CORNER))
        md_points.extend((p[0], p[1], CURVE) for p in points[1:-1])
        edges.append(
            {
                "name": names[i],
                "line": i,
                "start": list(points[0]),
                "end": list(points[-1]),
                "curved": len(points) > 2,
                "predicted_length_mm": round(edge_length(points), 2),
            }
        )

    xs = [p[0] for p in polygon] + [s[0] for e in edges_points for s in edge_samples(e)]
    ys = [p[1] for p in polygon] + [s[1] for e in edges_points for s in edge_samples(e)]
    return {
        "name": name.strip(),
        "points": md_points,
        "edges": edges,
        "bbox": {"min": [min(xs), min(ys)], "max": [max(xs), max(ys)]},
        "area_mm2": round(abs(area), 1),
        "winding": "counter-clockwise" if area > 0 else "clockwise",
    }


def translate(drafted: dict, dx: float, dy: float) -> dict:
    """The same plan moved by (dx, dy) on the 2D board."""
    moved = dict(drafted)
    moved["points"] = [(x + dx, y + dy, kind) for x, y, kind in drafted["points"]]
    moved["edges"] = [
        {**edge, "start": [edge["start"][0] + dx, edge["start"][1] + dy],
         "end": [edge["end"][0] + dx, edge["end"][1] + dy]}
        for edge in drafted["edges"]
    ]
    low, high = drafted["bbox"]["min"], drafted["bbox"]["max"]
    moved["bbox"] = {"min": [low[0] + dx, low[1] + dy], "max": [high[0] + dx, high[1] + dy]}
    return moved


def board_box(center: list[float], size: dict) -> dict:
    """A piece's 2D box from GetPatternPiecePos (its centre) and GetBoundingBoxOfPattern."""
    width, height = float(size["Width"]), float(size["Height"])
    return {
        "min": [center[0] - width / 2.0, center[1] - height / 2.0],
        "max": [center[0] + width / 2.0, center[1] + height / 2.0],
    }


def _overlaps(a: dict, b: dict) -> bool:
    return all(a["min"][k] < b["max"][k] and b["min"][k] < a["max"][k] for k in (0, 1))


def lay_out(
    drafted: dict, existing: list[dict], at: list[float] | None = None,
    gap: float = DEFAULT_GAP_MM,
) -> tuple[dict, list[str]]:
    """Move a planned piece to its place on the 2D board.

    ``at`` puts the piece's bottom-left there, and is kept even when it overlaps, with
    a warning naming what it overlaps. Without it the piece goes ``gap`` to the right of
    everything on the board, bottoms aligned -- deterministic, and never on top of
    anything. Returns the moved plan and any warnings.
    """
    low = drafted["bbox"]["min"]
    if at is not None:
        target = _point(at, "'at'")
    elif existing:
        target = (
            max(box["max"][0] for box in existing) + gap,
            min(box["min"][1] for box in existing),
        )
    else:
        target = (low[0], low[1])
    moved = translate(drafted, target[0] - low[0], target[1] - low[1])
    warnings = [
        f"it overlaps piece {box.get('name', box.get('index'))!r} on the 2D board"
        for box in existing
        if _overlaps(moved["bbox"], box)
    ]
    return moved, warnings


# --- read back ------------------------------------------------------------------------


def parse_input_information(text: str | dict) -> dict:
    """GetPatternInputInformation's JSON string, as {points: [[x, y]], lengths: [mm]}."""
    import json

    document = json.loads(text) if isinstance(text, str) else text
    entry = document["Pattern InputInformation"][0]
    lines = [line for line in entry.get("LineList", []) if "Line index" in line]
    points = [point for point in entry.get("PointList", []) if "Point index" in point]
    lines.sort(key=lambda line: int(line["Line index"]))
    points.sort(key=lambda point: int(point["Point index"]))
    return {
        "points": [
            [float(p["Point positionX"]), float(p["Point positionY"])] for p in points
        ],
        "lengths": [float(line["Line length"]) for line in lines],
    }


def verify(drafted: dict, read_back: dict) -> tuple[list[str], list[str], list[float]]:
    """Compare what MD built with what was planned.

    Returns ``(errors, warnings, md_lengths)``. An error means the edge map cannot be
    trusted -- MD's lines are not the planned edges -- and nothing should be sewn by
    name until it is resolved. A warning means MD built the planned lines and measures a
    curve differently from the prediction; MD's figure is the one to use.
    """
    errors: list[str] = []
    warnings: list[str] = []
    edges = drafted["edges"]
    corners, lengths = read_back["points"], read_back["lengths"]
    if len(lengths) != len(edges) or len(corners) != len(edges):
        errors.append(
            f"MD reports {len(lengths)} lines and {len(corners)} straight points for a "
            f"piece planned with {len(edges)} edges, so its line numbers are not the "
            "planned edges"
        )
        return errors, warnings, lengths
    for edge, corner in zip(edges, corners):
        off = math.dist(edge["start"], corner)
        if off > POSITION_TOLERANCE_MM:
            errors.append(
                f"edge {edge['name']!r} should start at {edge['start']} and MD's straight "
                f"point {edge['line']} is at {corner} ({off:.2f} mm away)"
            )
    for edge, md_length in zip(edges, lengths):
        predicted = edge["predicted_length_mm"]
        allowed = (
            max(CURVE_TOLERANCE_MM, CURVE_TOLERANCE_RATIO * predicted)
            if edge["curved"]
            else POSITION_TOLERANCE_MM
        )
        if abs(md_length - predicted) > allowed:
            message = (
                f"edge {edge['name']!r} (line {edge['line']}) measures {md_length:.2f} mm "
                f"in MD against {predicted:.2f} mm planned"
            )
            (warnings if edge["curved"] else errors).append(message)
    return errors, warnings, lengths


# --- the edge map kept inside MD ------------------------------------------------------

#: The key md-mcp keeps its edge maps under, in the garment's metadata object
#: (utility_api.ChangeMetaDataValueForCurrentGarment). It travels in a .zpac, so a
#: checkpoint restores the names with the garment; NewProject clears it.
METADATA_KEY = "md_mcp.pieces"
METADATA_VERSION = 1


def load_store(text: str) -> dict:
    """The stored edge maps, or an empty store for an unset or foreign value."""
    import json

    try:
        store = json.loads(text) if text else {}
    except ValueError:
        return {"version": METADATA_VERSION, "pieces": {}}
    if not isinstance(store, dict) or store.get("version") != METADATA_VERSION:
        return {"version": METADATA_VERSION, "pieces": {}}
    store.setdefault("pieces", {})
    return store


def store_entry(drafted: dict, md_lengths: list[float]) -> dict:
    """What is kept for one piece: its edges, and its corners as MD read them back."""
    return {
        "edges": {edge["name"]: edge["line"] for edge in drafted["edges"]},
        "corners": [edge["start"] for edge in drafted["edges"]],
        "lengths_mm": [round(length, 2) for length in md_lengths],
    }


def still_matches(entry: dict, corners: list[list[float]]) -> str | None:
    """Why a stored edge map no longer describes a piece, or None if it still does.

    A piece moved on the 2D board keeps its map; one whose shape changed does not,
    because its line numbers may have changed with it.
    """
    stored = entry.get("corners") or []
    if len(stored) != len(corners):
        return (
            f"it was drafted with {len(stored)} corners and now has {len(corners)}, so "
            "its line numbers have changed"
        )
    if not stored:
        return None
    dx = corners[0][0] - stored[0][0]
    dy = corners[0][1] - stored[0][1]
    for number, (was, now) in enumerate(zip(stored, corners)):
        if math.dist((was[0] + dx, was[1] + dy), now) > POSITION_TOLERANCE_MM * 20:
            return (
                f"corner {number} has moved relative to the others since it was "
                "drafted, so the piece's shape is not the one its edge names describe"
            )
    return None
