"""The avatar and the garment as meshes: body measurements, edges in 3D, fit.

MD's call API reports no 3D geometry at all, but ``ExportOBJ`` does, and more than it
seems (measured 2026-10-12 on 2026.0.315):

* millimetres, Y up, +Z the avatar's front;
* one ``g`` group per pattern piece, named after it;
* **every garment vertex carries a ``vt`` that is its 2D pattern position, in mm,
  relative to the centre of the piece's 2D bounding box** -- the corner drafted at
  (-310, 0) of a piece spanning y 0..580 came back as ``vt -310 -290``. So a point named
  on the 2D pattern can be found in 3D: that is what turns an edge name into a curve
  in space, and a seam into two curves that should meet.

Plain Python, no MD: ``bridge`` has MD export the files into a folder md-mcp chose
(the bridge is loopback-only, so MD always runs on this machine), and everything here
reads them. Slices are exact -- each triangle crossing a height contributes a segment,
and segments join into closed loops through the mesh edges they cross -- because
vertex bands were measured to fail on MD's avatars: at 1150 mm on Genesis 8 the sparse
vertices split the torso into three "pieces".
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]


@dataclass
class Group:
    name: str
    faces: list[tuple[int, ...]] = field(default_factory=list)
    uv_faces: list[tuple[int, ...]] = field(default_factory=list)


@dataclass
class Mesh:
    vertices: list[Vec3]
    uvs: list[tuple[float, float]]
    groups: list[Group]

    def triangles(self, groups: list[str] | None = None) -> list[tuple[int, int, int]]:
        found = []
        for group in self.groups:
            if groups is not None and group.name not in groups:
                continue
            for face in group.faces:
                for k in range(1, len(face) - 1):
                    found.append((face[0], face[k], face[k + 1]))
        return found


def read_obj(path: str) -> Mesh:
    """An OBJ as MD writes it: v, vt, g and f lines; anything else is ignored."""
    vertices: list[Vec3] = []
    uvs: list[tuple[float, float]] = []
    groups: list[Group] = []
    current: Group | None = None
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("v "):
                parts = line.split()
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif line.startswith("vt "):
                parts = line.split()
                uvs.append((float(parts[1]), float(parts[2])))
            elif line.startswith("g ") or line.startswith("o "):
                name = line[2:].strip() or "garment"
                if current is None or current.name != name or current.faces:
                    current = Group(name)
                    groups.append(current)
            elif line.startswith("f "):
                if current is None:
                    current = Group("garment")
                    groups.append(current)
                corners, uv_corners = [], []
                for token in line.split()[1:]:
                    fields = token.split("/")
                    corners.append(_index(fields[0], len(vertices)))
                    uv = fields[1] if len(fields) > 1 and fields[1] else ""
                    uv_corners.append(_index(uv, len(uvs)) if uv else -1)
                current.faces.append(tuple(corners))
                current.uv_faces.append(tuple(uv_corners))
    # MD writes the group line twice; keep the groups that hold faces, merged by name.
    merged: dict[str, Group] = {}
    for group in groups:
        if group.faces:
            target = merged.setdefault(group.name, Group(group.name))
            target.faces += group.faces
            target.uv_faces += group.uv_faces
    return Mesh(vertices, uvs, list(merged.values()))


def _index(token: str, count: int) -> int:
    value = int(token)
    return value - 1 if value > 0 else count + value


# --- slicing ---------------------------------------------------------------------------


@dataclass
class Loop:
    points: list[tuple[float, float]]  # (x, z), closed implicitly
    closed: bool

    @property
    def x_range(self) -> tuple[float, float]:
        xs = [p[0] for p in self.points]
        return min(xs), max(xs)

    @property
    def z_range(self) -> tuple[float, float]:
        zs = [p[1] for p in self.points]
        return min(zs), max(zs)

    @property
    def centre(self) -> tuple[float, float]:
        xs, zs = zip(*self.points)
        return sum(xs) / len(xs), sum(zs) / len(zs)

    @property
    def length(self) -> float:
        pts = self.points + (self.points[:1] if self.closed else [])
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))

    @property
    def girth(self) -> float:
        """What a tape measure reads: the perimeter of the loop's convex hull."""
        hull = convex_hull(self.points)
        return sum(math.dist(hull[i], hull[(i + 1) % len(hull)]) for i in range(len(hull)))

    def arcs(self) -> tuple[float, float]:
        """The tape girth split at the side seams -- the widest points in x -- into the
        front (+z) and back (-z) arcs. A body that projects at the bust has the larger
        front arc, and fronts drafted to an equal quarter are pulled apart over it."""
        hull = convex_hull(self.points)
        count = len(hull)
        right = max(range(count), key=lambda i: hull[i][0])
        left = min(range(count), key=lambda i: hull[i][0])

        def walk(start, stop):
            length, zs, i = 0.0, [], start
            while i != stop:
                j = (i + 1) % count
                length += math.dist(hull[i], hull[j])
                zs.append(hull[j][1])
                i = j
            return length, sum(zs) / len(zs) if zs else 0.0

        one, two = walk(right, left), walk(left, right)
        front, back = (one, two) if one[1] > two[1] else (two, one)
        return front[0], back[0]

    def contains(self, x: float, z: float) -> bool:
        inside = False
        pts = self.points
        for i in range(len(pts)):
            (x1, z1), (x2, z2) = pts[i], pts[(i + 1) % len(pts)]
            if (z1 > z) != (z2 > z):
                if x < x1 + (z - z1) * (x2 - x1) / (z2 - z1):
                    inside = not inside
        return inside

    def distance(self, x: float, z: float) -> float:
        best = math.inf
        pts = self.points
        for i in range(len(pts)):
            best = min(best, _segment_distance((x, z), pts[i], pts[(i + 1) % len(pts)]))
        return best


def _segment_distance(p, a, b) -> float:
    dx, dz = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dz * dz
    if length2 == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / length2))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dz))


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


class Slicer:
    """Horizontal sections of a mesh, exact, at any height."""

    def __init__(self, mesh: Mesh, groups: list[str] | None = None) -> None:
        self.vertices = mesh.vertices
        tris = mesh.triangles(groups)
        spans = []
        for tri in tris:
            ys = [self.vertices[i][1] for i in tri]
            spans.append((min(ys), max(ys), tri))
        spans.sort()
        self._spans = spans
        self._lows = [s[0] for s in spans]
        self._longest = max((s[1] - s[0] for s in spans), default=0.0)

    def loops(self, height: float) -> list[Loop]:
        # Nudge off exact vertex heights, where a triangle would touch rather than cross.
        h = height + 1e-6
        start = bisect.bisect_left(self._lows, h - self._longest)
        stop = bisect.bisect_right(self._lows, h)
        segments = []
        for low, high, tri in self._spans[start:stop]:
            if high < h:
                continue
            crossings = []
            for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                ya, yb = self.vertices[a][1], self.vertices[b][1]
                if (ya > h) != (yb > h):
                    t = (h - ya) / (yb - ya)
                    va, vb = self.vertices[a], self.vertices[b]
                    point = (va[0] + t * (vb[0] - va[0]), va[2] + t * (vb[2] - va[2]))
                    crossings.append(((min(a, b), max(a, b)), point))
            if len(crossings) == 2:
                segments.append(crossings)
        return _chain(segments)


def _chain(segments: list) -> list[Loop]:
    """Join section segments, each a pair of (edge key, 2D point), into loops."""
    links: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for number, ((key_a, _), (key_b, _)) in enumerate(segments):
        links.setdefault(key_a, []).append((number, 0))
        links.setdefault(key_b, []).append((number, 1))
    used = [False] * len(segments)
    loops = []
    for start_number in range(len(segments)):
        if used[start_number]:
            continue
        used[start_number] = True
        first, second = segments[start_number]
        chain = [first[1], second[1]]
        key = second[0]
        closed = False
        while True:
            nxt = [(n, end) for n, end in links.get(key, []) if not used[n]]
            if not nxt:
                closed = key == first[0]
                break
            n, end = nxt[0]
            used[n] = True
            other = segments[n][1 - end]
            key = other[0]
            if key == first[0]:
                closed = True
                break
            chain.append(other[1])
        if len(chain) >= 3:
            loops.append(Loop(chain, closed))
    return loops


def plane_section(vertices: list[Vec3], triangles: list[tuple[int, int, int]],
                  origin: Vec3, normal: Vec3) -> tuple[list[Loop], Vec3, Vec3]:
    """Sections through any plane, as loops in the plane's own (u, v) millimetres.

    Returns the loops and the u and v directions, so a 3D point maps into the same
    coordinates as (dot(p - origin, u), dot(p - origin, v)).
    """
    n = _unit(normal)
    helper = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _unit(_cross(n, helper))
    v = _cross(n, u)
    side = [_dot(_sub(p, origin), n) + 1e-6 for p in vertices]
    segments = []
    for tri in triangles:
        crossings = []
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            if (side[a] > 0) != (side[b] > 0):
                t = side[a] / (side[a] - side[b])
                va, vb = vertices[a], vertices[b]
                point = _sub(tuple(va[k] + t * (vb[k] - va[k]) for k in range(3)), origin)
                crossings.append(((min(a, b), max(a, b)), (_dot(point, u), _dot(point, v))))
        if len(crossings) == 2:
            segments.append(crossings)
    return _chain(segments), u, v


def best_plane(points: list[Vec3]) -> tuple[Vec3, Vec3]:
    """The centroid and normal of the plane nearest the points (least squares)."""
    count = len(points)
    centre = tuple(sum(p[k] for p in points) / count for k in range(3))
    d = [_sub(p, centre) for p in points]
    cov = [[sum(q[i] * q[j] for q in d) for j in range(3)] for i in range(3)]

    def dominant(matrix, skip=None):
        vec = (0.577, 0.578, 0.579)
        for _ in range(200):
            if skip is not None:
                vec = _sub(vec, tuple(_dot(vec, skip) * c for c in skip))
            vec = _unit(tuple(sum(matrix[i][j] * vec[j] for j in range(3)) for i in range(3)))
        return vec

    first = dominant(cov)
    second = dominant(cov, first)
    return centre, _unit(_cross(first, second))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    size = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / size, a[1] / size, a[2] / size)


# --- the body -----------------------------------------------------------------------

#: Heights are scanned in this step when looking for landmarks.
SCAN_STEP_MM = 5.0


def _torso(loops: list[Loop]) -> Loop | None:
    """The loop the body's centre line passes through, if any: the torso."""
    central = [loop for loop in loops if loop.x_range[0] <= 0.0 <= loop.x_range[1]]
    if not central:
        return None
    return max(central, key=lambda loop: loop.x_range[1] - loop.x_range[0])


def body_landmarks(mesh: Mesh) -> dict:
    """Landmark heights and torso girths, found from the avatar's own shape.

    Deterministic and pose-agnostic only as far as described: it expects a standing
    avatar, Y up and centred on x = 0, as MD's are. Found by scanning exact sections:

    * crotch: the highest height below which the centre line leaves the body (the legs
      part);
    * armpit: the lowest height above the crotch at which the torso section absorbs the
      arms -- its width jumps as they merge -- or, on a form without arms, where the
      torso narrows to the neck;
    * waist: the narrowest torso girth between them; hip: the widest below the waist;
      bust: the widest above it.
    """
    slicer = Slicer(mesh)
    ys = [v[1] for v in mesh.vertices]
    floor, top = min(ys), max(ys)
    height = top - floor
    scans = []
    h = floor + height * 0.30
    while h < floor + height * 0.92:
        loops = slicer.loops(h)
        torso = _torso(loops)
        scans.append((h, torso, loops))
        h += SCAN_STEP_MM

    crotch = None
    for h, torso, _ in scans:
        if torso is not None:
            crotch = h
            break
    if crotch is None:
        raise ValueError("no section crosses the avatar's centre line; is it standing at x = 0?")

    # The arms join the torso where its width jumps by more than a hand's breadth in one
    # step, looking upward from half way between crotch and top.
    armpit = None
    previous = None
    for h, torso, _ in scans:
        if h < crotch + 0.15 * height or torso is None:
            continue
        width = torso.x_range[1] - torso.x_range[0]
        if previous is not None and width - previous > 60.0:
            armpit = h
            break
        previous = width
    if armpit is None:
        # No arms join it (an armless form): the torso ends where it narrows to the neck.
        widest = 0.0
        for h, torso, _ in scans:
            if h < crotch or torso is None:
                continue
            width = torso.x_range[1] - torso.x_range[0]
            widest = max(widest, width)
            if h > crotch + 0.2 * height and width < 0.5 * widest:
                armpit = h
                break
    if armpit is None:
        armpit = floor + 0.75 * height

    torso_scans = [
        (h, torso) for h, torso, _ in scans
        if torso is not None and crotch + 20 <= h <= armpit - 20
    ]
    if not torso_scans:
        raise ValueError("found no torso between crotch and armpits")
    girths = [(h, torso.girth, torso) for h, torso in torso_scans]
    middle = [g for g in girths if crotch + 0.1 * (armpit - crotch) <= g[0]
              <= armpit - 0.2 * (armpit - crotch)]
    waist = min(middle or girths, key=lambda g: g[1])
    below = [g for g in girths if g[0] < waist[0]]
    above = [g for g in girths if g[0] > waist[0]]
    hip = max(below, key=lambda g: g[1]) if below else waist
    bust = max(above, key=lambda g: g[1]) if above else waist

    def describe(entry) -> dict:
        h, girth, torso = entry
        front, back = torso.arcs()
        return {
            "height": round(h, 1),
            "girth": round(girth, 1),
            "front_arc": round(front, 1),
            "back_arc": round(back, 1),
            "x": [round(v, 1) for v in torso.x_range],
            "z": [round(v, 1) for v in torso.z_range],
        }

    result = {
        "floor": round(floor, 1),
        "top": round(top, 1),
        "height": round(height, 1),
        "crotch": round(crotch, 1),
        "armpit": round(armpit, 1),
        "waist": describe(waist),
        "hip": describe(hip),
        "bust": describe(bust),
    }

    # Above the armpits the torso section shrinks steadily over the shoulders and then
    # collapses to the neck in one step: on Genesis 8, 717 mm at 1500 and 452 at 1510
    # (measured 2026-10-12). The neck base is the first section under 60 % of the bust.
    neck_scan = []
    h = armpit
    while h < top - 0.05 * height:
        torso = _torso(slicer.loops(h))
        if torso is not None:
            neck_scan.append((h, torso.girth, torso))
        h += SCAN_STEP_MM
    base = next((s for s in neck_scan if s[1] < 0.6 * bust[1]), None)
    if base is not None:
        result["neck_base"] = describe(base)
        above = [s for s in neck_scan if base[0] <= s[0] <= base[0] + 0.06 * height]
        if above:
            result["neck"] = describe(min(above, key=lambda s: s[1]))
        # The top of the body at each distance from the centre line, between the neck
        # and the arm: the line a shoulder seam rests on.
        low, high = armpit - 0.05 * height, base[0] + 0.02 * height
        half_neck = (base[2].x_range[1] - base[2].x_range[0]) / 2
        profile = []
        x = round(half_neck / 10) * 10
        while x < half_neck + 0.15 * height:
            ys = [v[1] for v in mesh.vertices
                  if abs(abs(v[0]) - x) < 5 and low < v[1] < high]
            if ys:
                profile.append([x, round(max(ys), 1)])
            x += 10
        result["shoulder_profile"] = profile
    return result


class Body:
    """The avatar as stacked sections, for inside and clearance tests."""

    def __init__(self, mesh: Mesh, step: float = 10.0) -> None:
        self.step = step
        self._vertices = mesh.vertices
        self._triangles = mesh.triangles()
        self._slicer = Slicer(mesh)
        self._cache: dict[int, list[Loop]] = {}

    def loops_near(self, y: float) -> list[Loop]:
        key = round(y / self.step)
        if key not in self._cache:
            self._cache[key] = [
                loop for loop in self._slicer.loops(key * self.step) if loop.closed
            ]
        return self._cache[key]

    def inside(self, point: Vec3) -> bool:
        return any(loop.contains(point[0], point[2]) for loop in self.loops_near(point[1]))

    def clearance(self, point: Vec3) -> float | None:
        """Horizontal distance to the body surface at the point's height; negative inside."""
        loops = self.loops_near(point[1])
        if not loops:
            return None
        distance = min(loop.distance(point[0], point[2]) for loop in loops)
        return -distance if self.inside(point) else distance

    def torso_girth(self, y: float) -> float | None:
        torso = _torso(self.loops_near(y))
        return torso.girth if torso else None

    def girth_inside(self, ring: list[Vec3]) -> float | None:
        """The girth of the body a ring of cloth goes round, measured in the ring's plane.

        A cuff on a sloping arm or a neckline dipping at the front cuts the body at an
        angle; a horizontal section at its mean height measures something else -- the
        torso and both arms for a cuff (measured 2026-10-12). None when the ring
        encloses no part of the body.
        """
        if len(ring) < 3:
            return None
        centre, normal = best_plane(ring)
        loops, u, v = plane_section(self._vertices, self._triangles, centre, normal)
        outline = Loop(convex_hull([(_dot(_sub(p, centre), u), _dot(_sub(p, centre), v))
                                    for p in ring]), True)
        # The body sections the ring goes round. Not the ring's centre inside the body:
        # a loose cuff hangs off-centre, and a Genesis 8 cuff's centre sat outside its
        # 167 mm wrist (measured 2026-10-12). A hand beyond the cuff is not inside it.
        around = [loop for loop in loops if loop.closed and outline.contains(*loop.centre)]
        if not around:
            return None
        # A tape round everything inside: a hem below the crotch goes round both legs.
        return Loop([point for loop in around for point in loop.points], True).girth


# --- pieces in 3D ---------------------------------------------------------------------


class Piece:
    """One pattern piece's mesh, with every vertex's 2D pattern position."""

    def __init__(self, mesh: Mesh, name: str) -> None:
        group = next((g for g in mesh.groups if g.name == name), None)
        if group is None:
            raise KeyError(name)
        self.name = name
        self.faces = group.faces
        self.uv_of: dict[int, tuple[float, float]] = {}
        for face, uv_face in zip(group.faces, group.uv_faces):
            for v, t in zip(face, uv_face):
                if t >= 0:
                    self.uv_of[v] = mesh.uvs[t]
        self.vertices = mesh.vertices
        self.boundary = self._boundary_loop()

    def _boundary_loop(self) -> list[int]:
        """The outline's vertices in order: the edges only one face uses."""
        count: dict[tuple[int, int], int] = {}
        for face in self.faces:
            for k in range(len(face)):
                a, b = face[k], face[(k + 1) % len(face)]
                key = (min(a, b), max(a, b))
                count[key] = count.get(key, 0) + 1
        neighbours: dict[int, list[int]] = {}
        for (a, b), n in count.items():
            if n == 1:
                neighbours.setdefault(a, []).append(b)
                neighbours.setdefault(b, []).append(a)
        if not neighbours:
            return []
        # The outline is the longest boundary loop; holes would be shorter ones.
        seen: set[int] = set()
        best: list[int] = []
        for start in neighbours:
            if start in seen:
                continue
            loop, previous, current = [start], None, start
            seen.add(start)
            while True:
                options = [n for n in neighbours[current] if n != previous]
                if not options or options[0] == start:
                    break
                previous, current = current, options[0]
                if current in seen:
                    break
                seen.add(current)
                loop.append(current)
            if len(loop) > len(best):
                best = loop
        return best

    def register_outline(self, lines: list[list[tuple[float, float]]]) -> dict:
        """Find where the 2D outline's lines are on the 3D outline, through any texture.

        ``lines`` is the piece's 2D outline, line by line from corner 0, each a dense
        polyline from its corner to the next. A vertex's ``vt`` is its 2D position only
        up to an affine map that depends on the fabric: millimetres for MD's default,
        a 518 mm jersey panel spanning -2..2, and a twill scaled differently along u and
        v (measured 2026-10-12) -- which is why matching arc length in uv failed by
        50 mm on a twill skirt. Normalising both outlines by their area moments turns
        any affine map into a rotation or reflection, which keeps arc length; every
        start vertex and both directions are then fitted over the whole outline, and the
        best wins. Ties -- a symmetric piece fits mirrored as well as it fits straight --
        go to the map closest to the texture's own axes.
        """
        loop = self.boundary
        n = len(loop)
        polygon = [p for line in lines for p in line[:-1]]
        if n < 3 or len(polygon) < 3:
            raise ValueError(f"{self.name}: too little outline to register")
        uv = [self.uv_of[v] for v in loop]
        c2, w2, w2_inverse = _whitening(polygon)
        cu, wu, _ = _whitening(uv)
        q2 = [_apply(w2, (p[0] - c2[0], p[1] - c2[1])) for p in polygon]
        qu = [_apply(wu, (p[0] - cu[0], p[1] - cu[1])) for p in uv]
        samples = 96
        target = [complex(*p) for p in _arc_samples(q2, 0, 1, samples)]
        uv_arc = _arc_table(qu)
        candidates = []
        for start in range(n):
            for direction in (1, -1):
                got = [complex(*p) for p in _arc_samples(qu, start, direction, samples, uv_arc)]
                for mirrored in (False, True):
                    src = [z.conjugate() for z in target] if mirrored else target
                    cross = sum(g * s.conjugate() for g, s in zip(got, src))
                    if abs(cross) < 1e-12:
                        continue
                    rotation = cross / abs(cross)
                    residual = sum(abs(g - rotation * s) ** 2 for g, s in zip(got, src))
                    candidates.append((residual, start, direction, mirrored, rotation))
        if not candidates:
            raise ValueError(f"{self.name}: could not register its outline")
        best_residual = min(c[0] for c in candidates)
        near = [c for c in candidates if c[0] <= best_residual * 1.02 + 1e-6]

        def map_for(candidate):
            _, _, _, mirrored, rotation = candidate

            def to_2d(point):
                q = complex(*_apply(wu, (point[0] - cu[0], point[1] - cu[1])))
                z = q / rotation
                if mirrored:
                    z = z.conjugate()
                p = _apply(w2_inverse, (z.real, z.imag))
                return (p[0] + c2[0], p[1] + c2[1])
            return to_2d

        def alignment(candidate) -> float:
            to_2d = map_for(candidate)
            o = to_2d(cu)
            ex = to_2d((cu[0] + 1.0, cu[1]))
            ey = to_2d((cu[0], cu[1] + 1.0))
            vx = (ex[0] - o[0], ex[1] - o[1])
            vy = (ey[0] - o[0], ey[1] - o[1])
            return vx[0] / (math.hypot(*vx) or 1) + vy[1] / (math.hypot(*vy) or 1)

        chosen = max(near, key=lambda c: (alignment(c), -c[0]))
        to_2d = map_for(chosen)
        self.direction = chosen[2]
        corners = [line[0] for line in lines]
        # The search is only as fine as its samples and the mesh's vertex spacing, so
        # the map it finds is close, not exact. With the corners known, every outline
        # vertex has a known partner -- an affine map keeps proportions along a line --
        # and a full affine map fitted to all of them is exact for MD's textures.
        for _ in range(3):
            mapped = [to_2d(p) for p in uv]
            self.corner_positions = [
                min(range(n), key=lambda k: math.dist(mapped[k], corner)) for corner in corners
            ]
            pairs = []
            for line_number, line in enumerate(lines):
                chain = self._positions_between(line_number)
                chain_uv = [uv[k] for k in chain]
                pairs += zip(chain_uv, _fractions_along(chain_uv, line))
            refined = _fit_affine(pairs)
            if refined is None:
                break
            to_2d = refined
        self.to_pattern = to_2d  # type: ignore[method-assign]
        mapped = [to_2d(p) for p in uv]
        segments = [(a, b) for line in lines for a, b in zip(line, line[1:])]
        errors = sorted(min(_segment_distance(m, a, b) for a, b in segments) for m in mapped)
        return {
            "residual_mm": round(errors[len(errors) // 2], 2),
            "worst_mm": round(errors[-1], 2),
            "corner_offsets_mm": [round(math.dist(mapped[k], c), 2)
                                  for k, c in zip(self.corner_positions, corners)],
        }

    def register(self, corners: list[tuple[float, float]], lengths: list[float]) -> dict:
        """Find the piece's corners on its 3D outline, whatever its texture mapping.

        A vertex's ``vt`` is its 2D pattern position only up to a similarity: in mm and
        centred on the piece's 2D box for MD's default fabric, but scaled by the texture
        size for a textured one -- a 518 mm jersey panel spanned -2..2 (measured
        2026-10-12) -- and a texture can be rotated or mirrored. So nothing is read off
        the uv in mm. The corners sit at known fractions of the outline's length (MD's
        own line lengths, from corner 0), and a similarity carries the 2D corners onto
        the uv outline: every start vertex and both directions are tried, and the one
        whose corners fit best wins. Returns the fit's residual, in pattern mm.
        """
        loop = self.boundary
        n = len(loop)
        if n < len(corners) or not corners:
            raise ValueError(f"{self.name}: its outline has {n} vertices for {len(corners)} corners")
        uv = [self.uv_of[v] for v in loop]
        arc = [0.0]
        for k in range(n):
            arc.append(arc[-1] + math.dist(uv[k], uv[(k + 1) % n]))
        perimeter = arc[-1] or 1.0
        total = sum(lengths) or 1.0
        fractions = [sum(lengths[:i]) / total for i in range(len(corners))]
        z2d = [complex(*c) for c in corners]
        mean2d = sum(z2d) / len(z2d)
        centred = [z - mean2d for z in z2d]
        spread = sum(abs(z) ** 2 for z in centred) or 1.0

        def snap(target: float) -> int:
            target %= perimeter
            k = bisect.bisect_left(arc, target)
            k = min(max(k, 1), n)
            return (k - 1) if target - arc[k - 1] <= arc[k] - target else k % n

        best = None
        for start in range(n):
            for direction in (1, -1):
                picks = [snap(arc[start] + direction * f * perimeter) for f in fractions]
                if len(set(picks)) != len(picks):
                    continue
                zuv = [complex(*uv[k]) for k in picks]
                meanuv = sum(zuv) / len(zuv)
                for mirrored in (False, True):
                    src = [z.conjugate() for z in centred] if mirrored else centred
                    a = sum((w - meanuv) * s.conjugate() for w, s in zip(zuv, src)) / spread
                    if abs(a) < 1e-12:
                        continue
                    residual = sum(abs((w - meanuv) - a * s) ** 2 for w, s in zip(zuv, src))
                    score = math.sqrt(residual / len(zuv)) / abs(a)
                    if best is None or score < best[0]:
                        best = (score, start, direction, picks, a, meanuv, mirrored)
        if best is None:
            raise ValueError(f"{self.name}: could not place its corners on its outline")
        score, _, direction, picks, a, meanuv, mirrored = best
        self.corner_positions = picks
        self.direction = direction
        self._transform = (a, meanuv, mean2d, mirrored)
        return {"residual_mm": round(score, 2), "mirrored_uv": mirrored}

    def to_pattern(self, uv: tuple[float, float]) -> tuple[float, float]:
        """A uv back to its 2D pattern position, through the fitted similarity."""
        a, meanuv, mean2d, mirrored = self._transform
        z = (complex(*uv) - meanuv) / a
        if mirrored:
            z = z.conjugate()
        z += mean2d
        return (z.real, z.imag)

    def _positions_between(self, line: int) -> list[int]:
        """Positions in the boundary loop from corner ``line`` to the next."""
        n = len(self.boundary)
        i = self.corner_positions[line]
        j = self.corner_positions[(line + 1) % len(self.corner_positions)]
        steps = ((j - i) * self.direction) % n
        if len(self.corner_positions) == 1:
            steps = n
        return [(i + self.direction * k) % n for k in range(steps + 1)]

    def line_chain(self, line: int) -> list[int]:
        """The outline's vertices from corner ``line`` to the next, as registered."""
        return [self.boundary[k] for k in self._positions_between(line)]

    def points(self, chain: list[int]) -> list[Vec3]:
        return [self.vertices[v] for v in chain]


def _fractions_along(chain, line):
    """For each chain point, the point the same fraction of the way along ``line``."""
    run = [0.0]
    for a, b in zip(chain, chain[1:]):
        run.append(run[-1] + math.dist(a, b))
    total = run[-1] or 1.0
    line_run = [0.0]
    for a, b in zip(line, line[1:]):
        line_run.append(line_run[-1] + math.dist(a, b))
    line_total = line_run[-1] or 1.0
    found = []
    for s in run:
        target = s / total * line_total
        k = min(max(bisect.bisect_right(line_run, target) - 1, 0), len(line) - 2)
        span = line_run[k + 1] - line_run[k] or 1.0
        t = (target - line_run[k]) / span
        a, b = line[k], line[k + 1]
        found.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return found


def _fit_affine(pairs):
    """Least-squares affine map uv -> 2D from (uv, 2D) pairs, or None if degenerate."""
    if len(pairs) < 3:
        return None
    # Normal equations for [x y 1] . coefficients, solved once for each output axis.
    s = [[0.0] * 3 for _ in range(3)]
    rx, ry = [0.0] * 3, [0.0] * 3
    for (u, v), (x, y) in pairs:
        row = (u, v, 1.0)
        for i in range(3):
            rx[i] += row[i] * x
            ry[i] += row[i] * y
            for j in range(3):
                s[i][j] += row[i] * row[j]

    def solve(m, r):
        m = [row[:] + [value] for row, value in zip(m, r)]
        for col in range(3):
            pivot = max(range(col, 3), key=lambda k: abs(m[k][col]))
            if abs(m[pivot][col]) < 1e-12:
                return None
            m[col], m[pivot] = m[pivot], m[col]
            for k in range(3):
                if k != col:
                    factor = m[k][col] / m[col][col]
                    m[k] = [a - factor * b for a, b in zip(m[k], m[col])]
        return [m[k][3] / m[k][k] for k in range(3)]

    cx, cy = solve(s, rx), solve(s, ry)
    if cx is None or cy is None:
        return None
    return lambda p: (cx[0] * p[0] + cx[1] * p[1] + cx[2], cy[0] * p[0] + cy[1] * p[1] + cy[2])


def _polygon_moments(points):
    """Area, centroid and second central moments of a simple polygon (Green's theorem)."""
    area = cx = cy = sxx = syy = sxy = 0.0
    n = len(points)
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
        sxx += (x0 * x0 + x0 * x1 + x1 * x1) * cross
        syy += (y0 * y0 + y0 * y1 + y1 * y1) * cross
        sxy += (x0 * y1 + 2 * x0 * y0 + 2 * x1 * y1 + x1 * y0) * cross
    area /= 2.0
    if abs(area) < 1e-12:
        raise ValueError("an outline with no area cannot be registered")
    cx /= 6.0 * area
    cy /= 6.0 * area
    sxx = sxx / (12.0 * area) - cx * cx
    syy = syy / (12.0 * area) - cy * cy
    sxy = sxy / (24.0 * area) - cx * cy
    return (cx, cy), (sxx, sxy, syy)


def _whitening(points):
    """Centroid, and the 2x2 maps to and from an outline with identity covariance."""
    centre, (a, b, d) = _polygon_moments(points)
    trace, det = a + d, a * d - b * b
    gap = math.sqrt(max(trace * trace / 4 - det, 0.0))
    l1, l2 = trace / 2 + gap, trace / 2 - gap
    if abs(b) > 1e-12:
        v1 = (l1 - d, b)
    else:
        v1 = (1.0, 0.0) if a >= d else (0.0, 1.0)
    norm = math.hypot(*v1)
    v1 = (v1[0] / norm, v1[1] / norm)
    v2 = (-v1[1], v1[0])
    s1, s2 = 1 / math.sqrt(max(l1, 1e-12)), 1 / math.sqrt(max(l2, 1e-12))
    whiten = ((s1 * v1[0] * v1[0] + s2 * v2[0] * v2[0], s1 * v1[0] * v1[1] + s2 * v2[0] * v2[1]),
              (s1 * v1[1] * v1[0] + s2 * v2[1] * v2[0], s1 * v1[1] * v1[1] + s2 * v2[1] * v2[1]))
    r1, r2 = math.sqrt(max(l1, 0.0)), math.sqrt(max(l2, 0.0))
    colour = ((r1 * v1[0] * v1[0] + r2 * v2[0] * v2[0], r1 * v1[0] * v1[1] + r2 * v2[0] * v2[1]),
              (r1 * v1[1] * v1[0] + r2 * v2[1] * v2[0], r1 * v1[1] * v1[1] + r2 * v2[1] * v2[1]))
    return centre, whiten, colour


def _apply(m, p):
    return (m[0][0] * p[0] + m[0][1] * p[1], m[1][0] * p[0] + m[1][1] * p[1])


def _arc_table(points):
    arc = [0.0]
    n = len(points)
    for k in range(n):
        arc.append(arc[-1] + math.dist(points[k], points[(k + 1) % n]))
    return arc


def _arc_samples(points, start, direction, count, arc=None):
    """``count`` points evenly spaced round a closed polyline from vertex ``start``."""
    arc = arc or _arc_table(points)
    n = len(points)
    total = arc[-1]
    found = []
    for k in range(count):
        s = (arc[start] + direction * total * k / count) % total
        i = min(max(bisect.bisect_right(arc, s) - 1, 0), n - 1)
        span = arc[i + 1] - arc[i] or 1.0
        t = (s - arc[i]) / span
        a, b = points[i], points[(i + 1) % n]
        found.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return found


def resample(points: list[Vec3], count: int) -> list[Vec3]:
    """``count`` points evenly spaced by length along a polyline, ends included."""
    if len(points) == 1:
        return points * count
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    total = lengths[-1] or 1.0
    found = []
    k = 0
    for n in range(count):
        target = total * n / (count - 1)
        while k < len(lengths) - 2 and lengths[k + 1] < target:
            k += 1
        span = lengths[k + 1] - lengths[k] or 1.0
        t = (target - lengths[k]) / span
        a, b = points[k], points[k + 1]
        found.append(tuple(a[d] + t * (b[d] - a[d]) for d in range(3)))
    return found


def polyline_length(points: list[Vec3]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _segment_hits_triangle(p0: Vec3, p1: Vec3, a: Vec3, b: Vec3, c: Vec3,
                           closed: bool = False) -> bool:
    """Möller-Trumbore on the segment p0..p1: its middle, or all of it when closed."""
    e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    e2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    d = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    h = (d[1] * e2[2] - d[2] * e2[1], d[2] * e2[0] - d[0] * e2[2], d[0] * e2[1] - d[1] * e2[0])
    det = e1[0] * h[0] + e1[1] * h[1] + e1[2] * h[2]
    if abs(det) < 1e-12:
        return False
    inv = 1.0 / det
    s = (p0[0] - a[0], p0[1] - a[1], p0[2] - a[2])
    u = inv * (s[0] * h[0] + s[1] * h[1] + s[2] * h[2])
    if u < 0.0 or u > 1.0:
        return False
    q = (s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2], s[0] * e1[1] - s[1] * e1[0])
    v = inv * (d[0] * q[0] + d[1] * q[1] + d[2] * q[2])
    if v < 0.0 or u + v > 1.0:
        return False
    t = inv * (e2[0] * q[0] + e2[1] * q[1] + e2[2] * q[2])
    return 0.0 <= t <= 1.0 if closed else 0.02 < t < 0.98


class TriangleIndex:
    """Triangles bucketed on a coarse grid, for segment tests that stay fast."""

    def __init__(self, vertices: list[Vec3], triangles: list[tuple[int, int, int]],
                 cell: float = 50.0) -> None:
        self.vertices = vertices
        self.cell = cell
        self.triangles = triangles
        self.buckets: dict[tuple[int, int, int], list[int]] = {}
        for number, tri in enumerate(triangles):
            pts = [vertices[i] for i in tri]
            lo = [min(p[d] for p in pts) for d in range(3)]
            hi = [max(p[d] for p in pts) for d in range(3)]
            for key in self._cells(lo, hi):
                self.buckets.setdefault(key, []).append(number)

    def _cells(self, lo, hi):
        c = self.cell
        for x in range(math.floor(lo[0] / c), math.floor(hi[0] / c) + 1):
            for y in range(math.floor(lo[1] / c), math.floor(hi[1] / c) + 1):
                for z in range(math.floor(lo[2] / c), math.floor(hi[2] / c) + 1):
                    yield (x, y, z)

    def crosses_closed(self, p0: Vec3, p1: Vec3) -> bool:
        """Whether the whole segment, ends included, meets any triangle."""
        lo = [min(p0[d], p1[d]) for d in range(3)]
        hi = [max(p0[d], p1[d]) for d in range(3)]
        tried: set[int] = set()
        for key in self._cells(lo, hi):
            for number in self.buckets.get(key, ()):
                if number in tried:
                    continue
                tried.add(number)
                a, b, c = (self.vertices[i] for i in self.triangles[number])
                if _segment_hits_triangle(p0, p1, a, b, c, closed=True):
                    return True
        return False

    def crosses(self, p0: Vec3, p1: Vec3, skip: set[int] = frozenset()) -> bool:
        lo = [min(p0[d], p1[d]) for d in range(3)]
        hi = [max(p0[d], p1[d]) for d in range(3)]
        tried: set[int] = set()
        for key in self._cells(lo, hi):
            for number in self.buckets.get(key, ()):
                if number in tried:
                    continue
                tried.add(number)
                tri = self.triangles[number]
                if skip and any(v in skip for v in tri):
                    continue
                a, b, c = (self.vertices[i] for i in tri)
                if _segment_hits_triangle(p0, p1, a, b, c):
                    return True
        return False
