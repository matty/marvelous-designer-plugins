"""Body measurements, outline registration and fit checks, on meshes built here.

Every mesh is written to a real OBJ file and read back through ``geometry.read_obj``, so
the parser is exercised along with the analysis. The shapes are simple enough that the
right answers are known exactly: an elliptical body with a waist, a gridded panel whose
texture coordinates are an affine image of its pattern -- scaled unevenly, rotated or
mirrored, as MD's were measured to be for textured fabrics (2026-10-12). Whether MD's
own meshes behave like these is ``tests/md_required``'s question.
"""

from __future__ import annotations

import math
import pathlib

import pytest

from md_mcp import fit, geometry

# --- mesh builders ------------------------------------------------------------------


def write_obj(path: pathlib.Path, groups: dict[str, tuple[list, list, list]]) -> geometry.Mesh:
    """groups: name -> (vertices, uvs or [], faces as 0-based vertex tuples)."""
    lines, v_base, t_base = [], 0, 0
    for name, (verts, uvs, faces) in groups.items():
        lines += [f"v {x} {y} {z}" for x, y, z in verts]
        lines += [f"vt {u} {v}" for u, v in uvs]
        lines.append(f"g {name}")
        lines.append(f"g {name}")  # MD writes each group line twice
        for face in faces:
            if uvs:
                lines.append("f " + " ".join(f"{v_base + i + 1}/{t_base + i + 1}/{v_base + i + 1}"
                                             for i in face))
            else:
                lines.append("f " + " ".join(str(v_base + i + 1) for i in face))
        v_base += len(verts)
        t_base += len(uvs)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return geometry.read_obj(str(path))


def tube(rings: list[tuple[float, float, float, float, float]], sides: int = 48):
    """A closed tube through (y, cx, cz, a, b) ellipses, capped at both ends."""
    verts, faces = [], []
    for y, cx, cz, a, b in rings:
        for k in range(sides):
            t = 2 * math.pi * k / sides
            verts.append((cx + a * math.cos(t), y, cz + b * math.sin(t)))
    for r in range(len(rings) - 1):
        for k in range(sides):
            p, q = r * sides + k, r * sides + (k + 1) % sides
            faces += [(p, q, q + sides), (p, q + sides, p + sides)]
    for ring, flip in ((0, True), (len(rings) - 1, False)):
        centre = len(verts)
        y, cx, cz, _, _ = rings[ring]
        verts.append((cx, y, cz))
        for k in range(sides):
            p, q = ring * sides + k, ring * sides + (k + 1) % sides
            faces.append((centre, q, p) if flip else (centre, p, q))
    return verts, faces


def merge(*parts):
    verts, faces = [], []
    for v, f in parts:
        base = len(verts)
        verts += v
        faces += [tuple(i + base for i in face) for face in f]
    return verts, faces


#: Torso half-widths by height: hips 180 at 900, waist 120 at 1150, bust 160 at 1350.
TORSO = [(860, 0, 0, 170, 120), (900, 0, 0, 180, 125), (1000, 0, 0, 160, 115),
         (1150, 0, 0, 120, 90), (1250, 0, 0, 140, 100), (1350, 0, 0, 160, 120),
         (1450, 0, 0, 150, 110), (1500, 0, 0, 60, 50), (1700, 0, 0, 60, 50)]


def body_mesh(tmp_path) -> geometry.Mesh:
    legs = [tube([(0, side * 90, 0, 60, 60), (870, side * 90, 0, 80, 80)])
            for side in (-1, 1)]
    verts, faces = merge(tube(TORSO), *legs)
    return write_obj(tmp_path / "body.obj", {"Mesh": (verts, [], faces)})


def ellipse_perimeter(a: float, b: float, sides: int = 48) -> float:
    pts = [(a * math.cos(2 * math.pi * k / sides), b * math.sin(2 * math.pi * k / sides))
           for k in range(sides)]
    return sum(math.dist(pts[k], pts[(k + 1) % sides]) for k in range(sides))


# --- the body ------------------------------------------------------------------------


def test_a_section_is_a_closed_loop_with_the_polygons_perimeter(tmp_path):
    mesh = body_mesh(tmp_path)
    loops = geometry.Slicer(mesh).loops(1150)
    assert len(loops) == 1 and loops[0].closed
    assert loops[0].girth == pytest.approx(ellipse_perimeter(120, 90), rel=1e-6)


def test_legs_are_separate_loops_below_the_crotch(tmp_path):
    loops = geometry.Slicer(body_mesh(tmp_path)).loops(500)
    assert len(loops) == 2
    assert sorted(round(loop.centre[0]) for loop in loops) == [-90, 90]


def test_landmarks_find_waist_hip_and_bust(tmp_path):
    marks = geometry.body_landmarks(body_mesh(tmp_path))
    # The torso's lowest ring is at 860, where the centre line first meets the body.
    assert marks["crotch"] == pytest.approx(860, abs=geometry.SCAN_STEP_MM + 1)
    assert marks["waist"]["height"] == pytest.approx(1150, abs=geometry.SCAN_STEP_MM + 1)
    assert marks["waist"]["girth"] == pytest.approx(ellipse_perimeter(120, 90), rel=0.01)
    assert marks["hip"]["height"] == pytest.approx(900, abs=geometry.SCAN_STEP_MM + 1)
    assert marks["bust"]["height"] == pytest.approx(1350, abs=geometry.SCAN_STEP_MM + 1)


def test_inside_and_clearance(tmp_path):
    body = geometry.Body(body_mesh(tmp_path))
    assert body.inside((0, 1150, 0)) and not body.inside((0, 1150, 120))
    assert body.clearance((0, 1150, 110)) == pytest.approx(20, abs=1)
    assert body.clearance((0, 1150, 80)) < 0


# --- pieces ---------------------------------------------------------------------------

TRAPEZOID = [(-300, 0), (300, 0), (180, 580), (-180, 580)]  # hem, side_r, waist, side_l


def panel(corners, uv_map, embed, steps=(24, 20)):
    """A gridded quadrilateral panel: vertices at embed(p), uvs at uv_map(p)."""
    nx, ny = steps
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = corners
    verts, uvs, faces = [], [], []
    for j in range(ny + 1):
        t = j / ny
        for i in range(nx + 1):
            s = i / nx
            bottom = (x0 + s * (x1 - x0), y0 + s * (y1 - y0))
            top = (x3 + s * (x2 - x3), y3 + s * (y2 - y3))
            p = (bottom[0] + t * (top[0] - bottom[0]), bottom[1] + t * (top[1] - bottom[1]))
            verts.append(embed(p))
            uvs.append(uv_map(p))
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            faces += [(a, a + 1, a + nx + 2), (a, a + nx + 2, a + nx + 1)]
    return verts, uvs, faces


def lines_of(corners):
    return [[corners[i], corners[(i + 1) % len(corners)]] for i in range(len(corners))]


def lengths_of(corners):
    return [math.dist(corners[i], corners[(i + 1) % len(corners)]) for i in range(len(corners))]


def front(p):  # a panel standing in front of the body, 2D +x to the avatar's left
    return (p[0], p[1] + 600, 150.0)


def back(p):  # behind, mirrored as MD arranged it
    return (-p[0], p[1] + 600, -180.0)


UV_MAPS = {
    "millimetres": lambda p: (p[0], p[1] - 290),
    "jersey-like": lambda p: (p[0] / 130.0, (p[1] - 290) / 130.0),
    "uneven twill": lambda p: (p[0] / 90.0 + 3.0, (p[1] - 290) / 210.0 - 1.0),
    "rotated": lambda p: (0.01 * (p[0] * math.cos(0.4) - p[1] * math.sin(0.4)),
                          0.01 * (p[0] * math.sin(0.4) + p[1] * math.cos(0.4))),
}


@pytest.mark.parametrize("mapping", sorted(UV_MAPS))
def test_corners_are_found_through_any_affine_texture(tmp_path, mapping):
    verts, uvs, faces = panel(TRAPEZOID, UV_MAPS[mapping], front)
    mesh = write_obj(tmp_path / "g.obj", {"front": (verts, uvs, faces)})
    piece = fit.PieceMap(mesh, "front", lines_of(TRAPEZOID), lengths_of(TRAPEZOID))
    assert piece.registration["worst_mm"] < 0.5, piece.registration
    for line, (a, b) in enumerate(lines_of(TRAPEZOID)):
        pts = piece.line_points(line)
        assert math.dist(pts[0], front(a)) < 0.5 and math.dist(pts[-1], front(b)) < 0.5
    axes = fit.pattern_axes(piece)
    assert axes == {"x": "the avatar's left (+x)", "y": "up (+y)", "face": "away from the body"}


def test_a_symmetric_piece_is_not_registered_mirrored():
    # A rectangle fits its own 180-degree turn exactly; the texture's axes decide.
    rect = [(-200, 0), (200, 0), (200, 300), (-200, 300)]
    verts, uvs, faces = panel(rect, lambda p: (p[0] / 50.0, p[1] / 80.0), front)
    mesh = geometry.Mesh(verts, uvs, [geometry.Group("r", faces, faces)])
    piece = fit.PieceMap(mesh, "r", lines_of(rect), lengths_of(rect))
    assert math.dist(piece.line_points(0)[0], front(rect[0])) < 0.5


def test_a_mirrored_texture_is_registered_mirrored(tmp_path):
    # Only a piece no affine map turns into its own mirror image can show it. Any
    # trapezoid can be -- its parallel sides keep their ratio -- so a trapezoid with a
    # mirrored texture is resolved toward the texture's axes, which is how MD's are.
    lopsided = [(-300, 0), (300, 40), (260, 580), (-90, 520)]
    verts, uvs, faces = panel(lopsided, lambda p: (-p[0] / 70.0, p[1] / 70.0), front)
    mesh = write_obj(tmp_path / "g.obj", {"front": (verts, uvs, faces)})
    piece = fit.PieceMap(mesh, "front", lines_of(lopsided), lengths_of(lopsided))
    assert piece.registration["worst_mm"] < 0.5
    assert math.dist(piece.line_points(1)[0], front(lopsided[1])) < 0.5


def skirt(tmp_path, back_shift=(0.0, 0.0, 0.0)):
    mm = UV_MAPS["uneven twill"]
    f = panel(TRAPEZOID, mm, front)
    b = panel(TRAPEZOID, mm, lambda p: tuple(c + s for c, s in zip(back(p), back_shift)))
    mesh = write_obj(tmp_path / "skirt.obj", {"front": f, "back": b})
    lines, lengths = lines_of(TRAPEZOID), lengths_of(TRAPEZOID)
    return mesh, {n: fit.PieceMap(mesh, n, lines, lengths) for n in ("front", "back")}


def side_seam(front_line: int, back_line: int, lengths) -> dict:
    start_f, start_b = sum(lengths[:front_line]), sum(lengths[:back_line])
    return {"name": "S", "pairs": [{
        "a": {"piece": "front", "from_mm": start_f, "to_mm": start_f + lengths[front_line],
              "lines": [front_line]},
        "b": {"piece": "back", "from_mm": start_b + lengths[back_line], "to_mm": start_b,
              "lines": [back_line]}}]}


def test_partners_are_compared_in_the_order_the_seam_joins_them(tmp_path):
    mesh, pieces = skirt(tmp_path)
    lengths = lengths_of(TRAPEZOID)
    # front's side_r (avatar's left) meets back's side_l, hem to hem: "opposite" ends.
    report = fit.seam_report(pieces, {"groups": [side_seam(1, 3, lengths)]},
                             geometry.TriangleIndex(mesh.vertices, mesh.triangles()), None,
                             mesh.vertices)[0]
    # The panels hang 330 mm apart in z and the edges line up in x and y.
    assert report["gap_mm"]["min"] == pytest.approx(330, abs=0.5)
    assert report["gap_mm"]["max"] == pytest.approx(330, abs=0.5)
    assert report["warnings"] == []


def test_a_seam_paired_the_wrong_way_round_is_flagged(tmp_path):
    mesh, pieces = skirt(tmp_path)
    lengths = lengths_of(TRAPEZOID)
    # front.side_r with back.side_r: the back's right side is at the avatar's right.
    report = fit.seam_report(pieces, {"groups": [side_seam(1, 1, lengths)]},
                             None, None, mesh.vertices)[0]
    assert report["gap_mm"]["median"] > 400


def test_a_path_through_cloth_is_counted(tmp_path):
    mesh, pieces = skirt(tmp_path)
    blocker = panel([(-500, 0), (500, 0), (500, 700), (-500, 700)], lambda p: p,
                    lambda p: (p[0], p[1] + 550, 0.0), steps=(4, 4))
    blocked = write_obj(tmp_path / "with_blocker.obj", {
        "front": panel(TRAPEZOID, UV_MAPS["uneven twill"], front),
        "back": panel(TRAPEZOID, UV_MAPS["uneven twill"], back),
        "blocker": blocker})
    lengths = lengths_of(TRAPEZOID)
    lines = lines_of(TRAPEZOID)
    pieces = {n: fit.PieceMap(blocked, n, lines, lengths) for n in ("front", "back")}
    report = fit.seam_report(pieces, {"groups": [side_seam(1, 3, lengths)]},
                             geometry.TriangleIndex(blocked.vertices, blocked.triangles()),
                             None, blocked.vertices)[0]
    assert report["paths_through_cloth"] == fit.SEAM_SAMPLES
    assert "pass through cloth" in report["warnings"][0]


def test_a_closed_seam_after_a_drape_has_no_open_length(tmp_path):
    mesh, pieces = skirt(tmp_path, back_shift=(0.0, 0.0, 330.0))  # back moved onto front
    lengths = lengths_of(TRAPEZOID)
    report = fit.seam_report(pieces, {"groups": [side_seam(1, 3, lengths)]}, None, None,
                             mesh.vertices)[0]
    assert report["gap_mm"]["max"] < 0.5 and report["open_mm"] == 0


def test_a_ring_reports_its_length_against_the_pattern(tmp_path):
    mesh, pieces = skirt(tmp_path)
    ring = fit.ring_report("waist", [("front", 2), ("back", 2)], pieces, None)
    assert ring["drafted_mm"] == pytest.approx(720.0)
    assert ring["length_mm"] == pytest.approx(720.0, abs=0.5)
    assert ring["stretch_pct"] == pytest.approx(0.0, abs=0.1)
    assert ring["height_mm"] == pytest.approx(1180.0, abs=0.5)


def test_piece_report_counts_cloth_inside_the_body(tmp_path):
    body = geometry.Body(body_mesh(tmp_path))
    inside = panel(TRAPEZOID, UV_MAPS["millimetres"], lambda p: (p[0] * 0.2, p[1] + 900, 0.0))
    mesh = write_obj(tmp_path / "g.obj", {"inside": inside})
    piece = fit.PieceMap(mesh, "inside", lines_of(TRAPEZOID), lengths_of(TRAPEZOID))
    report = fit.piece_report(piece, body)
    assert report["inside_body"] > 0 and report["deepest_inside_mm"] > 0


def test_pattern_axes_hold_on_a_slanted_piece(tmp_path):
    # A long leg panel leaning 100 mm over its height: pattern x and y are correlated,
    # which once made a front panel's x read as "down" (2026-10-12).
    leg = [(-95, 0), (95, 0), (-20 + 170, 1050), (-20, 1050)]
    leg = [(x + 100 * (1 - y / 1050), y) for x, y in leg]
    verts, uvs, faces = panel(leg, UV_MAPS["uneven twill"], front)
    mesh = write_obj(tmp_path / "g.obj", {"leg": (verts, uvs, faces)})
    piece = fit.PieceMap(mesh, "leg", lines_of(leg), lengths_of(leg))
    assert fit.pattern_axes(piece)["x"] == "the avatar's left (+x)"
    assert fit.pattern_axes(piece)["y"] == "up (+y)"


def test_cloth_the_body_passes_through_is_found_between_vertices(tmp_path):
    # A coarse panel straight through the torso: two of its vertices sit either side of
    # the body and none inside it, so only its edges can show the pass.
    body = body_mesh(tmp_path)
    coarse = panel([(-400, 0), (400, 0), (400, 60), (-400, 60)], UV_MAPS["millimetres"],
                   lambda p: (p[0], p[1] + 1120, 0.0), steps=(1, 1))
    mesh = write_obj(tmp_path / "g.obj", {"strip": coarse})
    strip = [(-400, 0), (400, 0), (400, 60), (-400, 60)]
    piece = fit.PieceMap(mesh, "strip", lines_of(strip), lengths_of(strip))
    report = fit.piece_report(piece, geometry.Body(body),
                              geometry.TriangleIndex(body.vertices, body.triangles()))
    assert report["inside_body"] == 0
    assert report["edges_through_body"] > 0


def limb(start, end, radius: float, sides: int = 48, steps: int = 8):
    """An open-ended cylinder between two points, at any angle."""
    axis = geometry._unit(geometry._sub(end, start))
    u = geometry._unit(geometry._cross(axis, (0.0, 0.0, 1.0)))
    v = geometry._cross(axis, u)
    verts, faces = [], []
    for s in range(steps + 1):
        c = tuple(start[k] + (end[k] - start[k]) * s / steps for k in range(3))
        for k in range(sides):
            t = 2 * math.pi * k / sides
            verts.append(tuple(c[i] + radius * (math.cos(t) * u[i] + math.sin(t) * v[i])
                               for i in range(3)))
    for s in range(steps):
        for k in range(sides):
            p, q = s * sides + k, s * sides + (k + 1) % sides
            faces += [(p, q, q + sides), (p, q + sides, p + sides)]
    return verts, faces


def test_a_ring_round_a_sloping_limb_measures_the_limb_not_the_torso(tmp_path):
    # A cuff on an arm sloping 45 degrees. A horizontal section at the cuff's mean
    # height cuts the torso and the arm obliquely; md_fit reported a Genesis 8 cuff's
    # "body girth" as 817 mm that way (2026-10-12). The ring's own plane cuts the arm.
    start, end = (150.0, 1400.0, 0.0), (550.0, 1000.0, 0.0)
    verts, faces = merge(tube(TORSO), limb(start, end, 40.0))
    body = geometry.Body(write_obj(tmp_path / "armed.obj", {"Mesh": (verts, [], faces)}))
    centre = (350.0, 1200.0, 0.0)
    axis = geometry._unit(geometry._sub(end, start))
    u = geometry._unit(geometry._cross(axis, (0.0, 0.0, 1.0)))
    v = geometry._cross(axis, u)
    ring = [tuple(centre[i] + 70 * (math.cos(t) * u[i] + math.sin(t) * v[i]) for i in range(3))
            for t in (2 * math.pi * k / 32 for k in range(32))]
    assert body.girth_inside(ring) == pytest.approx(ellipse_perimeter(40, 40), rel=0.01)
    # A loose cuff hangs: its centre is outside the limb it still goes round.
    hanging = [tuple(p[i] + 50 * v[i] for i in range(3)) for p in ring]
    assert body.girth_inside(hanging) == pytest.approx(ellipse_perimeter(40, 40), rel=0.01)
    # A ring round nothing has no body girth.
    away = [(p[0] + 400, p[1], p[2]) for p in ring]
    assert body.girth_inside(away) is None


def test_a_ring_round_both_legs_measures_round_both(tmp_path):
    # A jacket hem below the crotch: a tape round it goes round both legs at once.
    body = geometry.Body(body_mesh(tmp_path))
    ring = [(260 * math.cos(t), 700.0, 160 * math.sin(t))
            for t in (2 * math.pi * k / 40 for k in range(40))]
    legs = geometry.Slicer(body_mesh(tmp_path)).loops(700)
    assert len(legs) == 2
    both = geometry.Loop([p for leg in legs for p in leg.points], True).girth
    assert body.girth_inside(ring) == pytest.approx(both, rel=1e-6)
    assert both > legs[0].girth + 300  # one leg, plus the span between them twice


def test_arcs_split_the_girth_at_the_sides_front_from_back():
    # A D shape: a half ellipse projecting 150 mm forward, flat-ish behind (50 mm).
    pts = [(160 * math.cos(t), (150 if math.sin(t) > 0 else 50) * math.sin(t))
           for t in (2 * math.pi * k / 96 for k in range(96))]
    loop = geometry.Loop(pts, True)
    front, back = loop.arcs()
    assert front + back == pytest.approx(loop.girth, rel=1e-9)
    assert front > back + 100  # 487 against 353


FRONT_HALF = [(0, 0), (300, 0), (300, 600), (0, 600)]  # hem, side, top, cf


def test_two_pieces_through_each_other_are_found(tmp_path):
    # Two fronts arranged with their centre fronts overlapping, the second tilted so it
    # cuts through the first: the state that flung a jacket front round to its side
    # seam in the drape (2026-10-12).
    left = panel(FRONT_HALF, UV_MAPS["millimetres"], lambda p: (p[0] - 20, p[1] + 800, 150.0))
    right = panel(FRONT_HALF, UV_MAPS["millimetres"],
                  lambda p: (-p[0] + 20, p[1] + 800, 150.0 + (p[0] - 40) * 0.5))
    mesh = write_obj(tmp_path / "fronts.obj", {"front_L": left, "front_R": right})
    (crossing,) = fit.pieces_crossing(mesh, ["front_L", "front_R"])
    assert crossing["pieces"] == ["front_L", "front_R"] and crossing["edges"] > 10
    assert crossing["y"][0] < 820 and crossing["y"][1] > 1380
    # Layered -- the second 10 mm in front of the first -- they do not.
    layered = panel(FRONT_HALF, UV_MAPS["millimetres"], lambda p: (-p[0] + 20, p[1] + 800, 160.0))
    mesh = write_obj(tmp_path / "layered.obj", {"front_L": left, "front_R": layered})
    assert fit.pieces_crossing(mesh, ["front_L", "front_R"]) == []


def test_a_gap_is_measured_whichever_way_the_edges_run(tmp_path):
    # Open fronts 80 mm apart at the top, swinging to 200 mm at the hem.
    def embed(sign, spread):
        return lambda p: (sign * (p[0] + 40 + spread * (1 - p[1] / 600)), p[1] + 800, 150.0)
    # Drafted as the jacket's right front was: hem, then the centre front running up.
    mirrored = [(-300, 0), (0, 0), (0, 600), (-300, 600)]
    mesh = write_obj(tmp_path / "open.obj", {
        "front_L": panel(FRONT_HALF, UV_MAPS["millimetres"], embed(1, 60)),
        "front_R": panel(mirrored, UV_MAPS["millimetres"], lambda p: embed(-1, 60)((-p[0], p[1])))})
    pieces = {"front_L": fit.PieceMap(mesh, "front_L", lines_of(FRONT_HALF), lengths_of(FRONT_HALF)),
              "front_R": fit.PieceMap(mesh, "front_R", lines_of(mirrored), lengths_of(mirrored))}
    gap = fit.gap_report(("front_L", 3), ("front_R", 1), pieces,
                         {"front_L": {3: "cf"}, "front_R": {1: "cf"}})
    assert gap["edges"] == ["front_L.cf", "front_R.cf"]
    assert gap["gap_mm"]["min"] == pytest.approx(80, abs=1)
    assert gap["gap_mm"]["max"] == pytest.approx(200, abs=1)
    top, bottom = gap["by_height"][0], gap["by_height"][-1]
    assert top[0] > bottom[0] and top[1] < bottom[1]
