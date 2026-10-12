"""md_body, md_place and md_fit against a live Marvelous Designer, on its own avatar.

Genesis 8 Female ships with MD but loads with no arrangement points; MD's Adult V2
arrangement files, imported after it, give it 109 (measured 2026-10-12). So these run on
any install of this edition, with nobody's own files.

What they settle that nothing offline can: that MD's meshes register (texture
coordinates in whatever units the fabric gives them), that md_place can steer a piece
by MD's own arrangement values, and that md_fit tells an arrangement that will catch a
seam from one that will not -- the skirt pair below, which failed and drafted cleanly
on 2026-10-12. Needs an empty scene: it calls NewProject.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from mcp import Client

from md_mcp import bridge
from md_mcp.server import build_server

pytestmark = pytest.mark.md_required

ROOT = pathlib.Path("C:/Program Files/Marvelous Designer Enterprise Network Offline/Preset/Avatar")
AVATAR = ROOT / "Converting/Female/Genesis8_Female.avt"
VOLUMES = ROOT / "Arrangement/Adult_V2_Arrangement_Bounding_Volume.pan"
POINTS = ROOT / "Arrangement/Adult_V2_Arrangement_Point.arr"

PANEL = [{"at": [-310, 0], "edge": "hem"},
         {"at": [310, 0], "edge": "side_r", "through": [[265, 300]]},
         {"at": [185, 580], "edge": "waist"},
         {"at": [-185, 580], "edge": "side_l", "through": [[-265, 300]]}]


def _call(name: str, args: dict | None = None):
    import anyio

    async def run():
        async with Client(build_server()) as client:
            return await client.call_tool(name, args or {})

    result = anyio.run(run)
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


@pytest.fixture
def dressed(md):
    if not all(p.is_file() for p in (AVATAR, VOLUMES, POINTS)):
        pytest.skip("this MD install does not ship Genesis 8 and the Adult V2 arrangement files")
    reply = bridge.run_script(f'''
import utility_api, import_api, pattern_api, ApiTypes
utility_api.NewProject()
import_api.ImportAvatar(r"{AVATAR.as_posix()}", ApiTypes.ImportExportOption())
import_api.ImportFile(r"{VOLUMES.as_posix()}")
import_api.ImportFile(r"{POINTS.as_posix()}")
print(len(pattern_api.GetArrangementList()))
''')
    assert reply["output"].strip() == "109", reply
    yield
    bridge.call("utility_api.NewProject")


def _skirt(style: str) -> None:
    for name in ("front", "back"):
        _call("md_draft_piece", {"name": name, "outline": PANEL})
    for name, point in (("front", "Body_Front_Center_5"), ("back", "Body_Back_Center_5")):
        if style == "Flat":
            placed = _call("md_place", {"piece": name, "point": point, "height_mm": 1180,
                                        "anchor": "waist"})
            assert placed["problems"] == [], placed
        else:
            bridge.run_script(f'''
import pattern_api
index = [pattern_api.GetPatternPieceName(i) for i in range(pattern_api.GetPatternCount())].index("{name}")
pattern_api.SetArrangement(index, [p["ArrangementName"] for p in pattern_api.GetArrangementList()].index("{point}"))
pattern_api.SetArrangementShapeStyle(index, "Curved")
''')
    _call("md_sew", {"a": "front.side_r", "b": "back.side_l", "ends": "opposite"})
    _call("md_sew", {"a": "front.side_l", "b": "back.side_r", "ends": "opposite"})


def test_body_landmarks_are_in_order_and_plausible(dressed) -> None:
    body = _call("md_body")
    assert body["crotch"] < body["hip"]["height"] < body["waist"]["height"]
    assert body["waist"]["height"] < body["bust"]["height"] < body["armpit"]
    # Genesis 8 Female, measured 2026-10-12: waist 662, hip 1017, bust 937 mm.
    assert body["waist"]["girth"] < body["bust"]["girth"] < body["hip"]["girth"]
    assert 550 < body["waist"]["girth"] < 800


def test_place_reaches_the_height_and_clearance_asked(dressed) -> None:
    _call("md_draft_piece", {"name": "front", "outline": PANEL})
    placed = _call("md_place", {"piece": "front", "point": "Body_Front_Center_5",
                                "height_mm": 1180, "anchor": "waist", "clearance_mm": 20})
    assert placed["problems"] == []
    assert abs(placed["anchor_height_mm"] - 1180) <= 6
    assert placed["min_clearance_mm"] >= 20 and placed["inside_body"] == 0
    assert placed["pattern_axes"]["x"] == "the avatar's left (+x)"
    assert placed["pattern_axes"]["face"] == "away from the body"


def test_fit_flags_the_arrangement_that_caught_a_seam_open(dressed) -> None:
    _skirt("Curved")
    report = _call("md_fit")
    # Measured 2026-10-12: 513 front vertices inside the hips, and the hips through 278
    # of its mesh edges. Either is reported; the edges take precedence.
    assert any("through the cloth" in p or "inside the body" in p
               for p in report["problems"]), report["problems"]


def test_a_placed_skirt_checks_clean_and_drapes_closed(dressed) -> None:
    _skirt("Flat")
    rings = {"hem": ["front.hem", "back.hem"], "waist": ["front.waist", "back.waist"]}
    arranged = _call("md_fit", {"rings": rings})
    assert arranged["problems"] == [], arranged["problems"]
    bridge.call("utility_api.Simulate", [500])
    draped = _call("md_fit", {"rings": rings, "draped": True})
    assert draped["problems"] == [], draped["problems"]
    assert all(seam["open_mm"] == 0 for seam in draped["seams"])
    for piece in draped["pieces"].values():
        assert piece["registration_worst_mm"] < 1.0
    # The hem hangs free: its length is the pattern's.
    assert abs(draped["rings"]["hem"]["stretch_pct"]) < 2
