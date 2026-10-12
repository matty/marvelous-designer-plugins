"""Whole garments, end to end, against a live Marvelous Designer.

Each is drafted from md_body's measurements of MD's own Genesis 8, drafted and sewn by
name, placed with md_place, draped, and judged by md_fit -- the route a model takes
through md-mcp. They encode one pattern each, and that is the point: the first runs of
all four (2026-10-12) found real faults the tools now catch -- a skirt arranged Curved
caught a seam open, a T-shirt hung by its neckline, trousers whose straight legs the
avatar's splayed shins went through, a jacket whose cuffs and neckline md_fit measured
against the wrong slice of body -- and these pin the fixed routes down.

The patterns are test data, not md-mcp's: the tools decide how to build, never what.
Slow (a few minutes) and needing an empty scene, like every md_required test.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from mcp import Client

from md_mcp import bridge, drafting
from md_mcp.server import build_server

pytestmark = pytest.mark.md_required

ROOT = pathlib.Path("C:/Program Files/Marvelous Designer Enterprise Network Offline/Preset/Avatar")
AVATAR = ROOT / "Converting/Female/Genesis8_Female.avt"
VOLUMES = ROOT / "Arrangement/Adult_V2_Arrangement_Bounding_Volume.pan"
POINTS = ROOT / "Arrangement/Adult_V2_Arrangement_Point.arr"
FABRICS = pathlib.Path("C:/Users/Public/Documents/MarvelousDesigner/New Assets/Fabric")


def _call(name: str, args: dict | None = None):
    import anyio

    async def run():
        async with Client(build_server()) as client:
            return await client.call_tool(name, args or {})

    result = anyio.run(run)
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


@pytest.fixture
def body(md):
    if not all(p.is_file() for p in (AVATAR, VOLUMES, POINTS)):
        pytest.skip("this MD install does not ship Genesis 8 and the Adult V2 arrangement files")
    bridge.run_script(f'''
import utility_api, import_api, ApiTypes
utility_api.NewProject()
import_api.ImportAvatar(r"{AVATAR.as_posix()}", ApiTypes.ImportExportOption())
import_api.ImportFile(r"{VOLUMES.as_posix()}")
import_api.ImportFile(r"{POINTS.as_posix()}")
''')
    yield _call("md_body")
    bridge.call("utility_api.NewProject")


def _fabric(name: str) -> None:
    path = FABRICS / f"{name}.zfab"
    if not path.is_file():
        pytest.skip(f"no {path.name} in this install")
    bridge.run_script(f'''
import fabric_api, pattern_api
i = fabric_api.AddFabric(r"{path.as_posix()}")
for p in range(pattern_api.GetPatternCount()):
    pattern_api.SetPatternPieceFabricIndex(p, i)
''')


def _drape_clean(rings: dict, steps: int = 600) -> dict:
    arranged = _call("md_fit", {"rings": rings})
    assert arranged["problems"] == [], arranged["problems"]
    bridge.call("utility_api.Simulate", [steps])
    draped = _call("md_fit", {"rings": rings, "draped": True})
    assert draped["problems"] == [], draped["problems"]
    assert all(seam["open_mm"] == 0 for seam in draped["seams"])
    return draped


def test_an_elastic_waisted_skirt_sits_at_the_waist(body) -> None:
    waist, hip = body["waist"], body["hip"]
    w, h, length, drop = (waist["girth"] + 20) / 4, (hip["girth"] + 40) / 4, 580, 1180 - hip["height"]
    panel = [{"at": [-h - 40, 0], "edge": "hem"},
             {"at": [h + 40, 0], "edge": "side_r", "through": [[h, length - drop]]},
             {"at": [w, length], "edge": "waist"},
             {"at": [-w, length], "edge": "side_l", "through": [[-h, length - drop]]}]
    for name in ("front", "back"):
        _call("md_draft_piece", {"name": name, "outline": panel})
    _fabric("V2_Woven_Twill_1")
    for name, point in (("front", "Body_Front_Center_5"), ("back", "Body_Back_Center_5")):
        _call("md_place", {"piece": name, "point": point, "height_mm": 1180, "anchor": "waist"})
    for a, b in (("front.side_r", "back.side_l"), ("front.side_l", "back.side_r")):
        assert _call("md_sew", {"a": a, "b": b, "ends": "opposite"})["ok"]
    bridge.run_script('''
import pattern_api
for p in range(2):
    pattern_api.SetPatternPieceElastic(p, 2, True)
    pattern_api.SetPatternPieceElasticTotalLength(p, 2, 300.0)
''')
    draped = _drape_clean({"waist": ["front.waist", "back.waist"]})
    ring = draped["rings"]["waist"]
    # Measured 2026-10-12: 1149 mm against a 1157 mm waist; 1099 mm without elastic.
    assert abs(ring["height_mm"] - waist["height"]) < 30, ring
    assert ring["stretch_pct"] < 5, ring


def _length(outline: list, edge: str) -> float:
    return {e["name"]: e["predicted_length_mm"]
            for e in drafting.plan("p", outline)["edges"]}[edge]


def _sleeve(cap_r: float, cap_l: float, width: float, cuff: float, seam: float) -> list:
    """A set-in sleeve whose two cap halves are cap_r and cap_l long.

    The underarm seam is the arm's own length and the cap sits on top of it: a sleeve of
    fixed overall length lost seam as its cap grew, and the taut seam cut into the arm
    (2026-10-12). The apex leans toward the longer half -- the front, whose armhole
    scoops further.
    """
    def outline(h, apex):
        return [{"at": [-cuff, 0], "edge": "hem"}, {"at": [cuff, 0], "edge": "seam_r"},
                {"at": [width, seam], "edge": "cap_r",
                 "through": [[(width + apex) / 2, seam + h * 0.75]]},
                {"at": [apex, seam + h], "edge": "cap_l",
                 "through": [[(apex - width) / 2, seam + h * 0.75]]},
                {"at": [-width, seam], "edge": "seam_l"}]
    a_lo, a_hi = -0.6 * width, 0.6 * width
    for _ in range(30):
        apex = (a_lo + a_hi) / 2
        lo, hi = 1.0, 350.0
        for _ in range(40):
            h = (lo + hi) / 2
            both = _length(outline(h, apex), "cap_r") + _length(outline(h, apex), "cap_l")
            lo, hi = (h, hi) if both < cap_r + cap_l else (lo, h)
        # Moving the apex toward +x shortens cap_r.
        a_lo, a_hi = ((apex, a_hi) if _length(outline(h, apex), "cap_r") > cap_r
                      else (a_lo, apex))
    return outline(h, apex)


def test_a_t_shirt_drafted_to_the_arcs_drapes_closed(body) -> None:
    base = body["hip"]["height"]
    plateau = body["shoulder_profile"][0][1]
    tip = next((p for p in body["shoulder_profile"] if p[1] < plateau - 25), body["shoulder_profile"][-1])
    shoulder, uy = (tip[0], tip[1] + 15 - base), body["armpit"] - 70 - base
    neck_y = body["neck_base"]["height"] + 10 - base
    # Each panel to its own arc of the bust, 20 mm of ease on each half; the back 25 mm
    # more for the arm root, which an A-pose hangs close to. Quarters made a tunic.
    front_w = body["bust"]["front_arc"] / 2 + 20
    back_w = body["bust"]["back_arc"] / 2 + 45

    def panel(w, drop):
        n = 113.0
        return [{"at": [-w - 5, 0], "edge": "hem"}, {"at": [w + 5, 0], "edge": "side_r"},
                {"at": [w, uy], "edge": "armhole_r",
                 "through": [[w - 25, uy + 40], [shoulder[0] + 10, shoulder[1] - 70]]},
                {"at": list(shoulder), "edge": "shoulder_r"},
                {"at": [n, neck_y], "edge": "neck",
                 "through": [[n * 0.6, neck_y - drop * 0.8], [0, neck_y - drop]]},
                {"at": [-n, neck_y], "edge": "shoulder_l"},
                {"at": [-shoulder[0], shoulder[1]], "edge": "armhole_l",
                 "through": [[-shoulder[0] - 10, shoulder[1] - 70], [-w + 25, uy + 40]]},
                {"at": [-w, uy], "edge": "side_l"}]
    front, back = panel(front_w, 60), panel(back_w, 15)
    front_hole, back_hole = _length(front, "armhole_r"), _length(back, "armhole_l")
    # cap_r faces the back on the left sleeve and the front on the right (measured with
    # md_place's pattern_axes on Arm_Outside_1_L/R, 2026-10-12); 10 mm of ease a half.
    pieces = {"front": front, "back": back,
              "sleeve_L": _sleeve(back_hole + 10, front_hole + 10, 190, 190, 170),
              "sleeve_R": _sleeve(front_hole + 10, back_hole + 10, 190, 190, 170)}
    for name, outline in pieces.items():
        _call("md_draft_piece", {"name": name, "outline": outline})
    _fabric("V2_Cut_Sew_Knit_Jersey_1")
    axes = {}
    for name, point, args in (  # sleeves first, as the jacket measured
            ("sleeve_L", "Arm_Outside_1_L", {"style": "Curved", "clearance_mm": 10}),
            ("sleeve_R", "Arm_Outside_1_R", {"style": "Curved", "clearance_mm": 10}),
            ("front", "Body_Front_Center_3", {"height_mm": neck_y + base, "anchor": "shoulder_r"}),
            ("back", "Body_Back_Center_3", {"height_mm": neck_y + base, "anchor": "shoulder_l"})):
        placed = _call("md_place", {"piece": name, "point": point, **args})
        assert placed["problems"] == [], placed
        axes[name] = placed["pattern_axes"]
    assert "back" in axes["sleeve_L"]["x"] and "front" in axes["sleeve_R"]["x"], axes
    for a, b in (("front.shoulder_r", "back.shoulder_l"), ("front.shoulder_l", "back.shoulder_r"),
                 ("front.side_r", "back.side_l"), ("front.side_l", "back.side_r"),
                 ("sleeve_L.seam_r", "sleeve_L.seam_l"), ("sleeve_R.seam_r", "sleeve_R.seam_l"),
                 ("sleeve_L.cap_l", "front.armhole_r"), ("sleeve_L.cap_r", "back.armhole_l"),
                 ("sleeve_R.cap_r", "front.armhole_l"), ("sleeve_R.cap_l", "back.armhole_r")):
        assert _call("md_sew", {"a": a, "b": b, "ends": "opposite", "max_difference_mm": 15})["ok"]
    rings = {"hem": ["front.hem", "back.hem"], "neck": ["front.neck", "back.neck"],
             "armhole_L": ["front.armhole_r", "back.armhole_l"],
             "armhole_R": ["front.armhole_l", "back.armhole_r"]}
    draped = _drape_clean(rings)
    assert abs(draped["rings"]["hem"]["stretch_pct"]) < 6
    # Jersey hangs by its own weight: armholes 5-8 % longer than drafted with about
    # 100 mm of ease over the arm root, 70 or 100 mm below the armpit (2026-10-12).
    for armhole in ("armhole_L", "armhole_R"):
        assert draped["rings"][armhole]["ease_mm"] > 60 and draped["rings"][armhole]["stretch_pct"] < 10


def test_trousers_that_follow_the_legs_drape_clean(body) -> None:
    waist_y, hem_y = body["waist"]["height"], 100.0
    length = waist_y - hem_y
    crotch = length - (waist_y - body["crotch"]) - 25
    hip = length - (waist_y - body["hip"]["height"])
    wq, hq = (body["waist"]["girth"] + 30) / 4, (body["hip"]["girth"] + 60) / 4
    legs = _call("md_body", {"heights": [body["crotch"] - 30, hem_y + 30]})["sections"]
    top_c, hem_c = (max(loop["centre"][0] for loop in s["loops"]) for s in legs)
    knee_y = length * 0.45
    knee_c = top_c + (hem_c - top_c) * (crotch - knee_y) / crotch

    def panel(ext, extra):
        return [{"at": [hem_c - 95, 0], "edge": "hem"},
                {"at": [hem_c + 95, 0], "edge": "outseam",
                 "through": [[knee_c + 110, knee_y], [hq + extra, hip]]},
                {"at": [wq + extra, length], "edge": "waist"},
                {"at": [0, length], "edge": "rise",
                 "through": [[-3, crotch + 80], [-ext * 0.55, crotch + 18]]},
                {"at": [-ext, crotch], "edge": "inseam"}]

    def mirror(outline):
        return [{**c, "at": [-c["at"][0], c["at"][1]],
                 **({"through": [[-x, y] for x, y in c["through"]]} if "through" in c else {})}
                for c in outline]

    front, back = panel(55, 0), panel(105, 12)
    for name, outline in (("front_L", front), ("front_R", mirror(front)),
                          ("back_L", mirror(back)), ("back_R", back)):
        _call("md_draft_piece", {"name": name, "outline": outline})
    _fabric("V2_Woven_Twill_1")
    for name, point in (("front_L", "Leg_Front_L"), ("front_R", "Leg_Front_R"),
                        ("back_L", "Leg_Back_L"), ("back_R", "Leg_Back_R")):
        placed = _call("md_place", {"piece": name, "point": point, "height_mm": waist_y + 5,
                                    "anchor": "waist", "clearance_mm": 15})
        assert placed["problems"] == [], placed
    for a, b in (("front_L.outseam", "back_L.outseam"), ("front_R.outseam", "back_R.outseam"),
                 ("front_L.inseam", "back_L.inseam"), ("front_R.inseam", "back_R.inseam"),
                 ("front_L.rise", "front_R.rise"), ("back_L.rise", "back_R.rise")):
        assert _call("md_sew", {"a": a, "b": b, "ends": "same", "max_difference_mm": 20})["ok"]
    draped = _drape_clean({"hem_L": ["front_L.hem", "back_L.hem"]})
    assert abs(draped["rings"]["hem_L"]["stretch_pct"]) < 3


def test_shorts_drafted_to_the_arcs_drape_clean(body) -> None:
    # Drafted cold from md_body on 2026-10-12, with what the jacket taught: each panel to
    # its own arc of the body, the leg opening to the thigh measured at the hem.
    hem_y, waist_y = 700.0, body["waist"]["height"]
    length = waist_y - hem_y
    crotch, hip = body["crotch"] - 25 - hem_y, body["hip"]["height"] - hem_y
    thigh = max(loop["girth"] for loop in
                _call("md_body", {"heights": [hem_y]})["sections"][0]["loops"])

    def panel(arc, extension, opening, waist):  # one leg's half of the front or back
        hip_w = body["hip"][arc] / 2 + 15
        inseam_x = -extension * 0.6
        return [{"at": [inseam_x, 0], "edge": "hem"},
                {"at": [inseam_x + opening, 0], "edge": "outseam",
                 "through": [[hip_w, hip]]},
                {"at": [waist, length], "edge": "waist"},
                {"at": [0, length], "edge": "rise",
                 "through": [[-3, crotch + 70], [-extension * 0.55, crotch + 15]]},
                {"at": [-extension, crotch], "edge": "inseam"}]

    def mirror(outline):
        return [{**c, "at": [-c["at"][0], c["at"][1]],
                 **({"through": [[-x, y] for x, y in c["through"]]} if "through" in c else {})}
                for c in outline]
    # Genesis 8's hips are 481 mm round the front and 536 behind; the back takes more of
    # the leg opening too.
    def outseam(outline):
        return drafting.plan("p", outline)["edges"][1]["predicted_length_mm"]
    # The waist is shared between front and back so the outseams come out equal; drafted
    # to each arc alone the back's was 21 mm longer, and md_sew refused it.
    total = (body["waist"]["front_arc"] + body["waist"]["back_arc"]) / 2 + 16
    lo, hi = 0.0, total
    for _ in range(50):
        front_waist = (lo + hi) / 2
        front = panel("front_arc", 55, (thigh + 70) * 0.49, front_waist)
        back = panel("back_arc", 105, (thigh + 70) * 0.51, total - front_waist)
        # A narrower front waist slopes the front outseam more, and lengthens it.
        lo, hi = (lo, front_waist) if outseam(front) < outseam(back) else (front_waist, hi)
    for name, outline in (("front_L", front), ("front_R", mirror(front)),
                          ("back_L", mirror(back)), ("back_R", back)):
        _call("md_draft_piece", {"name": name, "outline": outline})
    _fabric("V2_Woven_Denim_1")
    for name, point in (("front_L", "Leg_Front_L"), ("front_R", "Leg_Front_R"),
                        ("back_L", "Leg_Back_L"), ("back_R", "Leg_Back_R")):
        placed = _call("md_place", {"piece": name, "point": point, "height_mm": waist_y + 5,
                                    "anchor": "waist", "clearance_mm": 15})
        assert placed["problems"] == [], placed
    for a, b in (("front_L.outseam", "back_L.outseam"), ("front_R.outseam", "back_R.outseam"),
                 ("front_L.inseam", "back_L.inseam"), ("front_R.inseam", "back_R.inseam"),
                 ("front_L.rise", "front_R.rise"), ("back_L.rise", "back_R.rise")):
        assert _call("md_sew", {"a": a, "b": b, "ends": "same", "max_difference_mm": 20})["ok"]
    # Elastic in the waist, as on the skirt: a plain one slid 74 mm onto the hips and
    # stretched 17 %, taking the hems 60 mm down with it (2026-10-12).
    bridge.run_script('''
import pattern_api
for p in range(pattern_api.GetPatternCount()):
    pattern_api.SetPatternPieceElastic(p, 2, True)
    pattern_api.SetPatternPieceElasticTotalLength(p, 2, 150.0)
''')
    rings = {"waist": ["front_L.waist", "front_R.waist", "back_L.waist", "back_R.waist"],
             "hem_L": ["front_L.hem", "back_L.hem"], "hem_R": ["front_R.hem", "back_R.hem"]}
    draped = _drape_clean(rings)
    print("SHORTS", json.dumps(draped["rings"]), json.dumps(draped["warnings"]))
    assert abs(draped["rings"]["waist"]["height_mm"] - waist_y) < 40, draped["rings"]["waist"]
    for hem in ("hem_L", "hem_R"):
        assert abs(draped["rings"][hem]["stretch_pct"]) < 3
        assert abs(draped["rings"][hem]["height_mm"] - hem_y) < 40, draped["rings"][hem]


def _jacket(body) -> dict:
    """A one-button tweed jacket: every panel to its own arc of the body."""
    base = body["hip"]["height"] - 60
    uy, wy, hy = (body["armpit"] - 130 - base, body["waist"]["height"] - base,
                  body["hip"]["height"] - base)
    plateau = body["shoulder_profile"][0][1]
    tip = next((p for p in body["shoulder_profile"] if p[1] < plateau - 25), body["shoulder_profile"][-1])
    shoulder, neck = (tip[0] + 10, tip[1] + 16 - base), (105.0, body["neck_base"]["height"] + 10 - base)
    cf = neck[1] - 40

    def widths(arc, extra=0.0):  # bust, waist, hip: half of each arc, plus ease
        return [body["bust"][arc] / 2 + 20 + extra, body["waist"][arc] / 2 + 25,
                body["hip"][arc] / 2 + 25]
    # Genesis 8's bust is 560 mm round the front and 378 behind (measured 2026-10-12):
    # quarters left the back 160 mm too wide and the fronts short of meeting. The back
    # has 25 mm more at the underarm for the arm root, which an A-pose hangs close to.
    fw, bw = widths("front_arc"), widths("back_arc", 25)

    def side(w, sign):  # hem corner, the seam's shaping, underarm corner
        return ([sign * (w[2] + 5), 0], {"through": [[sign * w[2], hy], [sign * w[1], wy]]},
                [sign * w[0], uy])

    def armhole(w, sign):
        pts = [[sign * (w[0] - 20), uy + 35], [sign * (shoulder[0] + 8), shoulder[1] - 75]]
        return {"through": pts if sign > 0 else pts[::-1]}

    length = _length

    def seam_length(w):
        return length([{"at": [0, 0], "edge": "a"},
                       {"at": side(w, 1)[0], "edge": "s", **side(w, 1)[1]},
                       {"at": side(w, 1)[2], "edge": "b"}, {"at": [0, uy], "edge": "c"}], "s")
    # The waist suppression is shared so the front and back side seams come out equal.
    total, lo, hi = fw[1] + bw[1], 0.0, fw[1] + bw[1]
    for _ in range(50):
        fw[1] = (lo + hi) / 2
        bw[1] = total - fw[1]
        lo, hi = (fw[1], hi) if seam_length(fw) > seam_length(bw) else (lo, fw[1])
    back = [{"at": side(bw, -1)[0], "edge": "hem"},
            {"at": side(bw, 1)[0], "edge": "side_r", **side(bw, 1)[1]},
            {"at": side(bw, 1)[2], "edge": "armhole_r", **armhole(bw, 1)},
            {"at": list(shoulder), "edge": "shoulder_r"},
            {"at": list(neck), "edge": "neck",
             "through": [[neck[0] * 0.6, neck[1] - 16], [0, neck[1] - 20]]},
            {"at": [-neck[0], neck[1]], "edge": "shoulder_l"},
            {"at": [-shoulder[0], shoulder[1]], "edge": "armhole_l", **armhole(bw, -1)},
            {"at": side(bw, -1)[2], "edge": "side_l", "through": side(bw, -1)[1]["through"][::-1]}]
    # Each front runs 20 mm past the centre, and its centre edge is split round a 30 mm
    # button tack at the waist: open above and below it, held there.
    front_l = [{"at": [-20, 0], "edge": "hem"},
               {"at": side(fw, 1)[0], "edge": "side_r", **side(fw, 1)[1]},
               {"at": side(fw, 1)[2], "edge": "armhole_r", **armhole(fw, 1)},
               {"at": list(shoulder), "edge": "shoulder_r"},
               {"at": list(neck), "edge": "neck", "through": [[neck[0] * 0.6, cf + 8]]},
               {"at": [-20, cf], "edge": "lapel"}, {"at": [-20, wy + 15], "edge": "button"},
               {"at": [-20, wy - 15], "edge": "cf_low"}]
    front_r = [{"at": side(fw, -1)[0], "edge": "hem"},
               {"at": [20, 0], "edge": "cf_low"}, {"at": [20, wy - 15], "edge": "button"},
               {"at": [20, wy + 15], "edge": "lapel"},
               {"at": [20, cf], "edge": "neck", "through": [[-neck[0] * 0.6, cf + 8]]},
               {"at": [-neck[0], neck[1]], "edge": "shoulder_l"},
               {"at": [-shoulder[0], shoulder[1]], "edge": "armhole_l", **armhole(fw, -1)},
               {"at": side(fw, -1)[2], "edge": "side_l",
                "through": side(fw, -1)[1]["through"][::-1]}]
    front_hole, back_hole = length(front_l, "armhole_r"), length(back, "armhole_l")

    # 5 mm of ease a cap half; 400 mm of underarm seam -- at 420 the right cuff reached
    # the hand (2026-10-12).
    return {"front_L": front_l, "front_R": front_r, "back": back,
            "sleeve_L": _sleeve(back_hole + 5, front_hole + 5, 185, 120, 400),
            "sleeve_R": _sleeve(front_hole + 5, back_hole + 5, 185, 120, 400)}


def test_a_one_button_jacket_drapes_clean_and_hangs_open(body) -> None:
    for name, outline in _jacket(body).items():
        _call("md_draft_piece", {"name": name, "outline": outline})
    _fabric("V2_Woven_Tweed_1")
    neck_y = body["neck_base"]["height"] + 10
    axes = {}
    # Sleeves first. Placed after the back, md_place moved them out to clear it, and
    # their underarm seams then closed through the arm (2026-10-12); placed first, the
    # back steps out of their way instead.
    for name, point, args in (
            ("sleeve_L", "Arm_Outside_1_L", {"style": "Curved", "clearance_mm": 15}),
            ("sleeve_R", "Arm_Outside_1_R", {"style": "Curved", "clearance_mm": 15}),
            ("front_L", "Body_Front_3_L", {"height_mm": neck_y, "anchor": "shoulder_r"}),
            ("front_R", "Body_Front_3_R", {"height_mm": neck_y, "anchor": "shoulder_l"}),
            ("back", "Body_Back_Center_3", {"height_mm": neck_y, "anchor": "shoulder_l"})):
        placed = _call("md_place", {"piece": name, "point": point, **args})
        assert placed["problems"] == [] and placed["through_pieces"] == {}, placed
        axes[name] = placed["pattern_axes"]
    assert "back" in axes["sleeve_L"]["x"] and "front" in axes["sleeve_R"]["x"], axes
    seams = [("front_L.shoulder_r", "back.shoulder_l"), ("front_R.shoulder_l", "back.shoulder_r"),
             ("front_L.side_r", "back.side_l"), ("front_R.side_l", "back.side_r"),
             ("sleeve_L.seam_r", "sleeve_L.seam_l"), ("sleeve_R.seam_r", "sleeve_R.seam_l"),
             ("front_L.button", "front_R.button"),
             ("sleeve_L.cap_l", "front_L.armhole_r"), ("sleeve_L.cap_r", "back.armhole_l"),
             ("sleeve_R.cap_r", "front_R.armhole_l"), ("sleeve_R.cap_l", "back.armhole_r")]
    for a, b in seams:  # 10 mm allowed: each cap half carries 5 mm of ease on purpose
        assert _call("md_sew", {"a": a, "b": b, "ends": "opposite", "max_difference_mm": 10})["ok"]
    rings = {"hem": ["front_L.hem", "back.hem", "front_R.hem"],
             "neck": ["front_L.neck", "back.neck", "front_R.neck"],
             "armhole_L": ["front_L.armhole_r", "back.armhole_l"],
             "armhole_R": ["front_R.armhole_l", "back.armhole_r"]}
    _drape_clean(rings, steps=700)
    report = _call("md_fit", {"rings": rings, "draped": True, "gaps": {
        "lapels": ["front_L.lapel", "front_R.lapel"],
        "below": ["front_L.cf_low", "front_R.cf_low"]}})
    ring, gap = report["rings"], report["gaps"]
    # Measured 2026-10-12: armholes 1-6 % over 477 mm drafted round a 290-370 mm arm
    # root; drafted with no ease over it they stretched 12 % and the arm came through.
    assert abs(ring["hem"]["stretch_pct"]) < 2
    assert all(ring[a]["stretch_pct"] < 5 for a in ("armhole_L", "armhole_R"))
    # Open, not swinging: the lapels part by up to 105 mm and meet at the button. With
    # no button the fronts swung 250-480 mm apart and carried the side seams into the arms.
    assert gap["lapels"]["gap_mm"]["min"] < 5 and gap["lapels"]["gap_mm"]["max"] < 160
    # Below the button the fronts flare, by 17 to 80 mm in runs of this pattern.
    assert gap["below"]["gap_mm"]["max"] < 120
    # The neckline carries the jacket -- 10-18 % stretched however wide or deep it was
    # drafted, with no collar or facing to hold it -- and md_fit says so.
    assert any("ring neck" in w for w in report["warnings"])
