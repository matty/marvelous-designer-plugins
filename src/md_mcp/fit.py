"""Whether a garment's pieces, seams and openings sit where they should, read in 3D.

Built on ``geometry``: the avatar and garment meshes MD exports, every garment vertex
tied to its 2D pattern position. Three questions, each answered from the meshes and
never from MD's return values:

* **Seams.** Each side of every seam is found in 3D -- the stretch of outline the
  ledger names -- and the two are compared point for point, in the order the seam
  joins them. Before a drape this says whether the arrangement lets them meet: a gap is
  expected, but a straight path between partners that runs *through cloth or the body*
  is how the 2026-10-12 skirt, arranged "Curved" with its side edges crossed, came out
  with one side seam stuck open 100 mm. After a drape, a gap that remains is a seam
  that did not close.
* **Pieces.** Vertices inside the body, and clearance from it.
* **Rings.** Edges that go round the body together -- a waistline made of front.waist
  and back.waist -- measured as one: their 3D length against the body's girth at the
  height they sit, which is the ease the garment actually has there.

Plain Python; ``server`` supplies the meshes and MD's piece and seam read-backs.
"""

from __future__ import annotations

import math
from typing import Any

from md_mcp import geometry

#: Points compared along each seam.
SEAM_SAMPLES = 16
#: After a drape, partners further apart than this have not been pulled together.
OPEN_GAP_MM = 10.0
#: Cloth this close to a path's ends is the edge itself, not something in the way.
END_ZONE_MM = 25.0
#: Cloth resting on skin reads a fraction of a millimetre inside it now and then -- a
#: draped T-shirt had one vertex 0.4 mm in (2026-10-12). Deeper is a problem.
INSIDE_TOLERANCE_MM = 2.0

#: A ring stretched more than this after a drape is under load, or a knit. Woven hems
#: measured 0-1 % and a jersey one 2 %; a loaded armhole 12 %, a jacket neckline it hung
#: from 10-18 %, and jersey armholes with 100 mm of ease 5-8 % (2026-10-12).
RING_STRETCH_PCT = 5.0


class PieceMap:
    """One piece in 3D, with its outline measured the way MD's ledger measures it."""

    def __init__(self, mesh: geometry.Mesh, name: str, lines: list, lengths: list):
        """``lines``: the 2D outline, line by line from corner 0 -- see outline_lines."""
        self.piece = geometry.Piece(mesh, name)
        self.name = name
        self.corners = [tuple(line[0]) for line in lines]
        self.lengths = list(lengths)
        self.registration = self.piece.register_outline(lines)
        self.chains = [self.piece.line_chain(i) for i in range(len(self.corners))]

    def line_points(self, line: int) -> list[geometry.Vec3]:
        return self.piece.points(self.chains[line])

    def span_points(self, from_mm: float, to_mm: float) -> list[geometry.Vec3]:
        """The outline between two distances from corner 0, as the ledger gives them.

        Each line's own vertices are spread over MD's length for that line, so a span
        ending part way along a line ends part way along its vertices.
        """
        low, high = sorted((from_mm, to_mm))
        found: list[geometry.Vec3] = []
        start = 0.0
        for line, length in enumerate(self.lengths):
            end = start + length
            if end > low and start < high and line < len(self.chains):
                pts = self.line_points(line)
                a = max(0.0, (low - start) / length) if length else 0.0
                b = min(1.0, (high - start) / length) if length else 1.0
                piece = _between(pts, a, b)
                if found and piece and math.dist(found[-1], piece[0]) < 1e-6:
                    piece = piece[1:]
                found += piece
            start = end
        return found if from_mm <= to_mm else list(reversed(found))

    def vertex_ids(self) -> list[int]:
        return list(self.piece.uv_of)


def outline_lines(document: dict) -> dict[str, list]:
    """Each piece's 2D outline from MD's pattern JSON, line by line, curves sampled.

    A line's points are its corner, its curve points and the next corner; MD draws a
    spline through them, which drafting.edge_samples reproduces to 0.01 mm in length.
    Keyed by piece name; a name used twice is left out rather than guessed.
    """
    from md_mcp import drafting

    found: dict[str, list] = {}
    seen: dict[str, int] = {}
    for piece in document.get("PatternList", []):
        name = piece.get("Name")
        seen[name] = seen.get(name, 0) + 1
        lines = []
        for line in (piece.get("ShapeInfo") or {}).get("LineList") or []:
            points = [(p["Position"]["x"], p["Position"]["y"]) for p in line.get("PointList", [])]
            if len(points) >= 2:
                lines.append(drafting.edge_samples(points, per_span=8))
        found[name] = lines
    return {name: lines for name, lines in found.items() if seen[name] == 1}


def _between(points: list, a: float, b: float) -> list:
    if a <= 0.0 and b >= 1.0:
        return list(points)
    lengths = [0.0]
    for p, q in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.dist(p, q))
    total = lengths[-1] or 1.0

    def at(t):
        target = t * total
        for k in range(len(points) - 1):
            if lengths[k + 1] >= target:
                span = lengths[k + 1] - lengths[k] or 1.0
                u = (target - lengths[k]) / span
                return tuple(points[k][d] + u * (points[k + 1][d] - points[k][d])
                             for d in range(3))
        return points[-1]

    inner = [p for p, s in zip(points, lengths) if a * total < s < b * total]
    return [at(a), *inner, at(b)]


def _stats(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)

    def q(f):
        return round(ordered[min(len(ordered) - 1, int(f * len(ordered)))], 1)

    return {"min": round(ordered[0], 1), "p10": q(0.1), "median": q(0.5), "p90": q(0.9),
            "max": round(ordered[-1], 1)}


def edge_label(piece: str, lines, names: dict[str, dict[int, str]] | None) -> str:
    """"front.side_r" where the edge has a stored name, "front line 3" where not."""
    lines = list(lines or [])
    known = (names or {}).get(piece, {})
    if lines and all(line in known for line in lines):
        return " + ".join(f"{piece}.{known[line]}" for line in lines)
    return f"{piece} line{'s' if len(lines) != 1 else ''} {', '.join(map(str, lines))}"


def seam_report(
    pieces: dict[str, PieceMap], ledger: dict, garment_index: geometry.TriangleIndex,
    body_index: geometry.TriangleIndex | None, vertices: list,
    names: dict[str, dict[int, str]] | None = None,
) -> list[dict]:
    """Each sewn pair, compared in 3D. ``names`` labels lines by their edge names."""
    reports = []
    for group in ledger.get("groups", []):
        for pair in group["pairs"]:
            a, b = pair["a"], pair["b"]
            if a.get("piece") not in pieces or b.get("piece") not in pieces:
                reports.append({"group": group["name"], "skipped": "a side's piece is not in the export"})
                continue
            pa = pieces[a["piece"]].span_points(a["from_mm"], a["to_mm"])
            pb = pieces[b["piece"]].span_points(b["from_mm"], b["to_mm"])
            if len(pa) < 2 or len(pb) < 2:
                reports.append({"group": group["name"], "skipped": "a side has no outline"})
                continue
            sa = geometry.resample(pa, SEAM_SAMPLES)
            sb = geometry.resample(pb, SEAM_SAMPLES)
            gaps = [math.dist(p, q) for p, q in zip(sa, sb)]
            flipped = [math.dist(p, q) for p, q in zip(sa, reversed(sb))]
            through_cloth = through_body = 0
            for p, q in zip(sa, sb):
                if math.dist(p, q) < 2 * OPEN_GAP_MM:
                    continue
                skip = {
                    v for v, point in enumerate(vertices)
                    if math.dist(point, p) < END_ZONE_MM or math.dist(point, q) < END_ZONE_MM
                } if garment_index else set()
                if garment_index and garment_index.crosses(p, q, skip):
                    through_cloth += 1
                if body_index and body_index.crosses(p, q):
                    through_body += 1
            mean, mean_flipped = sum(gaps) / len(gaps), sum(flipped) / len(flipped)
            report: dict[str, Any] = {
                "group": group["name"],
                "seam": f"{edge_label(a['piece'], a.get('lines'), names)} <-> "
                        f"{edge_label(b['piece'], b.get('lines'), names)}",
                "gap_mm": _stats(gaps),
                "open_mm": round(sum(
                    geometry.polyline_length([sa[k], sa[k + 1]])
                    for k in range(len(gaps) - 1)
                    if gaps[k] > OPEN_GAP_MM and gaps[k + 1] > OPEN_GAP_MM), 1),
                "paths_through_cloth": through_cloth,
                "paths_through_body": through_body,
                "samples": SEAM_SAMPLES,
            }
            # Risks, not verdicts. Measured 2026-10-12 on Genesis 8: T-shirt side seams
            # with 4 of 16 paths crossing an arm and a sleeve closed in the drape; a
            # skirt arranged Curved, paths through the hips and 513 vertices starting
            # inside the body, caught a side seam open 100 mm. Cloth inside the body is
            # the problem; a crossing path is a reason to look.
            warnings = []
            if through_cloth:
                warnings.append(
                    f"{through_cloth} of {SEAM_SAMPLES} straight paths between partners "
                    "pass through cloth: the edges may start crossed")
            if through_body:
                warnings.append(
                    f"{through_body} of {SEAM_SAMPLES} paths between partners pass "
                    "through the body: fine round a limb's end, a risk through the torso")
            if mean > 50 and mean_flipped < 0.5 * mean:
                warnings.append(
                    f"partners are {mean:.0f} mm apart on average, but {mean_flipped:.0f} "
                    "mm paired the other way round: check the seam's ends, or whether a "
                    "piece is arranged mirrored")
            report["warnings"] = warnings
            reports.append(report)
    return reports


_DIRECTIONS = {
    (0, 1): "the avatar's left (+x)", (0, -1): "the avatar's right (-x)",
    (1, 1): "up (+y)", (1, -1): "down (-y)",
    (2, 1): "the front (+z)", (2, -1): "the back (-z)",
}


def _describe(vector) -> str:
    axis = max(range(3), key=lambda d: abs(vector[d]))
    return _DIRECTIONS[(axis, 1 if vector[axis] > 0 else -1)]


def pattern_axes(piece: PieceMap) -> dict:
    """Which way the piece's 2D +x and +y run in 3D, and which way its drafted face looks.

    Fitted over every vertex: the 3D displacement per mm of pattern x and of pattern y.
    MD's 2D window shows a pattern's face side, and on a front piece 2D +x ran to the
    avatar's left and +y up, so the face looks along x cross y; a back piece arranged
    behind came back mirrored, +x to the avatar's right (measured 2026-10-12). This
    is what decides a seam's ends from the pieces as they actually sit.
    """
    ids = [v for v in piece.vertex_ids()]
    uvs = [piece.piece.to_pattern(piece.piece.uv_of[v]) for v in ids]
    pts = [piece.piece.vertices[v] for v in ids]
    mu = [sum(u[k] for u in uvs) / len(uvs) for k in range(2)]
    mp = [sum(p[d] for p in pts) / len(pts) for d in range(3)]
    # Both pattern axes at once: on a slanted piece -- a trouser leg following a splayed
    # shin -- pattern x and y are correlated, and fitting each alone mixes them (a front
    # panel's x came out "down", measured 2026-10-12).
    sxx = sum((u[0] - mu[0]) ** 2 for u in uvs)
    syy = sum((u[1] - mu[1]) ** 2 for u in uvs)
    sxy = sum((u[0] - mu[0]) * (u[1] - mu[1]) for u in uvs)
    det = sxx * syy - sxy * sxy or 1.0
    ax, ay = [], []
    for d in range(3):
        sxp = sum((u[0] - mu[0]) * (p[d] - mp[d]) for u, p in zip(uvs, pts))
        syp = sum((u[1] - mu[1]) * (p[d] - mp[d]) for u, p in zip(uvs, pts))
        ax.append((syy * sxp - sxy * syp) / det)
        ay.append((sxx * syp - sxy * sxp) / det)
    normal = (ax[1] * ay[2] - ax[2] * ay[1], ax[2] * ay[0] - ax[0] * ay[2],
              ax[0] * ay[1] - ax[1] * ay[0])
    outward = (mp[0], 0.0, mp[2])  # away from the body's vertical axis
    facing = sum(n * o for n, o in zip(normal, outward))
    return {
        "x": _describe(ax),
        "y": _describe(ay),
        "face": "away from the body" if facing > 0 else "toward the body (inside out)",
    }


def edges_through_body(piece: PieceMap, body_index: geometry.TriangleIndex) -> list:
    """The piece's mesh edges that cross the body's surface: cloth the body pokes through.

    A vertex inside test misses it when a limb passes between cloth vertices: trousers
    whose straight legs an A-posed avatar's splayed shins left measured 1-2 vertices
    inside the body, while the shin stood clear of the trouser leg below the knee
    (2026-10-12). An edge whose segment crosses the body's triangles is cloth on both
    sides of the skin, whatever its vertices say.
    """
    seen: set[tuple[int, int]] = set()
    crossing = []
    vertices = piece.piece.vertices
    for face in piece.piece.faces:
        for k in range(len(face)):
            a, b = face[k], face[(k + 1) % len(face)]
            key = (min(a, b), max(a, b))
            if key in seen:
                continue
            seen.add(key)
            if body_index.crosses_closed(vertices[a], vertices[b]):
                crossing.append(tuple((vertices[a][d] + vertices[b][d]) / 2 for d in range(3)))
    return crossing


def piece_report(piece: PieceMap, body: geometry.Body | None,
                 body_index: geometry.TriangleIndex | None = None) -> dict:
    ids = piece.vertex_ids()
    pts = [piece.piece.vertices[v] for v in ids]
    ys = [p[1] for p in pts]
    report: dict[str, Any] = {
        "vertices": len(ids),
        "y": [round(min(ys), 1), round(max(ys), 1)],
        "centroid": [round(sum(p[d] for p in pts) / len(pts), 1) for d in range(3)],
        "pattern_axes": pattern_axes(piece),
        # How well the 2D corners fitted the 3D outline, in pattern mm: near zero means
        # every edge below was found where MD put it.
        "registration_residual_mm": piece.registration["residual_mm"],
        "registration_worst_mm": piece.registration["worst_mm"],
    }
    if body is not None:
        measured = [(c, p) for c, p in ((body.clearance(p), p) for p in pts) if c is not None]
        clearances = [c for c, _ in measured]
        inside = [(c, p) for c, p in measured if c < 0]
        report["inside_body"] = len(inside)
        report["deepest_inside_mm"] = round(-min(c for c, _ in inside), 1) if inside else 0.0
        if inside:
            # Where, so a look in MD can go straight there.
            report["deepest_inside_at"] = [round(v, 1) for v in min(inside)[1]]
            heights = [p[1] for _, p in inside]
            report["inside_body_y"] = [round(min(heights), 1), round(max(heights), 1)]
        report["clearance_mm"] = _stats(clearances)
    if body_index is not None:
        crossing = edges_through_body(piece, body_index)
        report["edges_through_body"] = len(crossing)
        if crossing:
            heights = [p[1] for p in crossing]
            report["through_body_y"] = [round(min(heights), 1), round(max(heights), 1)]
            report["through_body_at"] = [round(v, 1) for v in crossing[len(crossing) // 2]]
    return report


def ring_report(name: str, edges: list[tuple[str, int]], pieces: dict[str, PieceMap],
                body: geometry.Body | None,
                names: dict[str, dict[int, str]] | None = None) -> dict:
    points: list = []
    length = 0.0
    drafted = 0.0
    for piece_name, line in edges:
        pts = pieces[piece_name].line_points(line)
        length += geometry.polyline_length(pts)
        drafted += pieces[piece_name].lengths[line]
        points += pts
    ys = [p[1] for p in points]
    mean_y = sum(ys) / len(ys)
    report: dict[str, Any] = {
        "edges": [edge_label(p, [line], names) for p, line in edges],
        "length_mm": round(length, 1),
        # Measured against the pattern: the clearest sign a line is under load. A
        # skirt waist in MD's twill preset settled 12 % longer than drafted, whatever
        # its ease (2026-10-12) -- the drape stretched it until it held the skirt up.
        "drafted_mm": round(drafted, 1),
        "stretch_pct": round(100.0 * (length / drafted - 1.0), 1) if drafted else None,
        "height_mm": round(mean_y, 1),
        "height_range_mm": [round(min(ys), 1), round(max(ys), 1)],
    }
    if body is not None:
        girth = body.girth_inside(points)
        if girth:
            report["body_girth_mm"] = round(girth, 1)
            # Cloth rides a few mm off the skin, which alone adds 2*pi*clearance:
            # about 30 mm at the 5 mm measured, so ease near that is a tight fit.
            report["ease_mm"] = round(length - girth, 1)
            # The pattern against the body: a ring drafted smaller than what it goes
            # round can only sit there stretched, or ride up or down until it fits.
            report["drafted_ease_mm"] = round(drafted - girth, 1)
    return report


#: Fewer edges than this through another piece is a seam's join, not a crossing: sewn
#: front and sleeve met through 1-2 edges at the armhole after a drape, while fronts
#: arranged through each other crossed by 84-146 (measured 2026-10-12).
CROSSING_EDGES = 5

#: Points compared along two edges that are left apart.
GAP_SAMPLES = 9


def gap_report(a: tuple[str, int], b: tuple[str, int], pieces: dict[str, PieceMap],
               names: dict[str, dict[int, str]] | None = None) -> dict:
    """How far apart two unsewn edges hang: an open front, a vent, a slit.

    Each edge is sampled evenly and partnered end to end, whichever way round puts them
    closer, so the answer does not depend on the direction either edge was drafted in.
    """
    pa = geometry.resample(pieces[a[0]].line_points(a[1]), GAP_SAMPLES)
    pb = geometry.resample(pieces[b[0]].line_points(b[1]), GAP_SAMPLES)
    forward = [math.dist(p, q) for p, q in zip(pa, pb)]
    backward = [math.dist(p, q) for p, q in zip(pa, reversed(pb))]
    if sum(backward) < sum(forward):
        pb, gaps = list(reversed(pb)), backward
    else:
        gaps = forward
    along = sorted(
        ([round((p[1] + q[1]) / 2, 1), round(g, 1)] for p, q, g in zip(pa, pb, gaps)),
        reverse=True)
    return {
        "edges": [edge_label(a[0], [a[1]], names), edge_label(b[0], [b[1]], names)],
        "gap_mm": _stats(gaps),
        # Top to bottom: [height, gap]. An open front swinging apart below the bust
        # shows here as gaps growing down the list.
        "by_height": along,
    }


def pieces_crossing(mesh: geometry.Mesh, names: list[str]) -> list[dict]:
    """Pairs of pieces whose cloth passes through each other: edges of one crossing the
    other's triangles, counted both ways.

    Two arranged fronts with centre-front extensions started 84 edges through each
    other down the whole front, and the drape flung one of them round to the side seam
    (measured 2026-10-12); nothing else in md_fit saw it. A seam's two sides meet only
    at their edges, which the open test does not count.
    """
    tris = {name: mesh.triangles([name]) for name in names}
    boxes = {}
    for name, found in tris.items():
        ids = {v for tri in found for v in tri}
        if ids:
            pts = [mesh.vertices[v] for v in ids]
            boxes[name] = ([min(p[d] for p in pts) for d in range(3)],
                           [max(p[d] for p in pts) for d in range(3)])
    indexes: dict[str, geometry.TriangleIndex] = {}

    def index(name):
        if name not in indexes:
            indexes[name] = geometry.TriangleIndex(mesh.vertices, tris[name])
        return indexes[name]

    def crossing(a, b):
        edges = {tuple(sorted(e)) for x, y, z in tris[a] for e in ((x, y), (y, z), (z, x))}
        return [e for e in edges
                if index(b).crosses(mesh.vertices[e[0]], mesh.vertices[e[1]])]

    found = []
    ordered = sorted(boxes)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            (alo, ahi), (blo, bhi) = boxes[a], boxes[b]
            if any(ahi[d] < blo[d] or bhi[d] < alo[d] for d in range(3)):
                continue
            hits = crossing(a, b) + crossing(b, a)
            if len(hits) >= CROSSING_EDGES:
                pts = [mesh.vertices[v] for e in hits for v in e]
                found.append({
                    "pieces": [a, b],
                    "edges": len(hits),
                    "y": [round(min(p[1] for p in pts), 1), round(max(p[1] for p in pts), 1)],
                    "x": [round(min(p[0] for p in pts), 1), round(max(p[0] for p in pts), 1)],
                })
    return found
