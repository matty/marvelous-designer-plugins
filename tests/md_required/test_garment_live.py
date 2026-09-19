"""Every garment domain, driven through the bridge against a real Marvelous Designer.

Avatar, drafting, sewing, fabric, simulation, export and both ways of adding a sewing
pattern -- each one built in MD and then *measured*, because the failures that matter
here are the quiet ones. ``Simulate(1)`` returns ``True`` and leaves the garment flat;
a seam MD accepts can still join a hem to a neckline. Both look exactly like success
from the outside, so nothing below trusts a return value it can read back instead.

Run with MD open and ``plugin/md_mcp_listener.py`` started in it::

    .venv\\Scripts\\python.exe -m pytest -m md_required

**These call ``NewProject``, which discards the open scene, and MD has no undo.**
"""

from __future__ import annotations

import json
import pathlib

import pytest

from md_mcp import bridge

pytestmark = pytest.mark.md_required

#: Ships with MD itself, so these do not depend on a downloaded library. Enterprise
#: Offline has no Library assets at all, which is also why nothing here asks for an
#: arrangement point (see ``docs/md-conventions.md``).
AVATAR = pathlib.Path(
    "C:/Program Files/Marvelous Designer Enterprise Network Offline"
    "/Preset/Avatar/Converting/Female/Genesis8_Female.avt"
)

#: Two 360 x 500 mm panels, side by side in 2D so they do not overlap. Wound
#: counter-clockwise from the bottom-left, so on each piece line 0 is the bottom, 1 the
#: right, 2 the top and 3 the left.
PANELS = """
import pattern_api, utility_api

def panel(x0, name):
    index = pattern_api.CreatePatternWithPoints(
        [(x0, 0.0, 0), (x0 + 360.0, 0.0, 0), (x0 + 360.0, 500.0, 0), (x0, 500.0, 0)]
    )
    pattern_api.SetPatternPieceName(index, name)
    return index

front = panel(-180.0, "front")
back = panel(220.0, "back")
print(front, back)
"""

#: The side seams of that pair: front's right edge to back's left, and the other way
#: round. Opposite direction flags, because two counter-clockwise pieces meeting face
#: to face traverse their shared seam in opposite directions.
SEW_THE_SIDES = """
import pattern_api
pattern_api.AddSeamlinePairGroup(0, 1, 1, 3, True, False)
pattern_api.AddSeamlinePairGroup(0, 3, 1, 1, True, False)
"""


@pytest.fixture
def empty(md):
    """A scene with nothing in it, proven rather than assumed."""
    bridge.call("utility_api.NewProject")
    assert bridge.scene()["pattern_count"] == 0
    return md


def export_obj(path: pathlib.Path) -> dict:
    """Export the garment alone and measure what actually came out of MD.

    The 3D shape cannot be read back through the pattern API -- it reaches a curve's
    endpoints and its total length and stops -- so the exported mesh is the only
    evidence about where the cloth ended up.
    """
    source = (
        "import ApiTypes, export_api\n"
        "option = ApiTypes.ImportExportOption()\n"
        "option.bExportAvatar = False\n"
        "option.bExportGarment = True\n"
        "print(export_api.ExportOBJ({!r}, option))\n".format(path.as_posix())
    )
    bridge.run_script(source, name="export.py")

    vertices = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("v "):
            vertices.append([float(value) for value in line.split()[1:4]])
    if not vertices:
        return {"vertices": 0, "points": []}
    return {
        "vertices": len(vertices),
        "points": vertices,
        "spans": [
            max(v[axis] for v in vertices) - min(v[axis] for v in vertices)
            for axis in range(3)
        ],
    }


def furthest_move(before: dict, after: dict) -> float:
    """How far the most-displaced vertex moved between two exports, in mm."""
    assert before["vertices"] == after["vertices"], "the mesh changed size"
    return max(
        sum(abs(a - b) for a, b in zip(first, second))
        for first, second in zip(before["points"], after["points"])
    )


# --------------------------------------------------------------- reaching the whole API


def test_the_search_gives_real_signatures_for_an_overloaded_call(md) -> None:
    # pybind writes "Foo(*args, **kwargs)" as the first line of every overloaded
    # function and puts the real signatures further down. Reporting that first line --
    # which md_api used to do -- described 77 calls in this build as taking anything at
    # all, AddSeamlinePairGroup and GetLineLength among them. Those are precisely the
    # calls where it matters, because MD dispatches on arity.
    (found,) = bridge.api("AddSeamlinePairGroup")

    assert len(found["signatures"]) == 3
    assert "arg4: bool, arg5: bool) -> bool" in found["signatures"][0]
    assert found.get("doc") != "AddSeamlinePairGroup(*args, **kwargs)"


def test_the_search_reports_the_fields_of_an_option_object(md) -> None:
    # The 37 calls that need an ApiTypes struct are unusable without knowing what is in
    # it, and MD reports that nowhere else -- there is no documentation for these
    # structs in the build at all. Constructing one and reading it back is the only way.
    (found,) = bridge.api("ImportZPRJOption")

    assert found["fields"]["bLoadAvatar"] == "True"
    assert found["fields"]["bAppend"] == "False"


def test_every_member_of_every_md_module_is_listed(md) -> None:
    # The promise this repo makes: nothing curated, nothing left out. If MD grows a
    # module or a call, this is what notices.
    listed = bridge.api("")
    modules = {entry["call"].split(".")[0] for entry in listed}

    assert modules == set(bridge.MD_MODULES)
    assert len(listed) > 650, len(listed)
    # The empty search is the compact index: names only, and deliberately without the
    # signatures, enum values and struct fields, which is ~99KB a caller who typed
    # nothing did not ask for. Those arrive with a term -- below.
    assert all(set(entry) == {"call"} for entry in listed)


def test_an_enum_member_carries_its_value_and_an_option_struct_its_fields(md) -> None:
    # Enum members are values, not calls, and are listed with their repr so they can be
    # recognised; option structs are listed with their fields and defaults, which MD
    # documents nowhere else. Both only come back for a search with a term in it.
    enum = next(e for e in bridge.api("CLOAPI_ANCHOR_CENTER"))
    struct = next(e for e in bridge.api("ImportZPRJOption"))

    assert enum["value"].endswith(": 0>"), enum
    assert struct["fields"]["bAppend"] == "False", struct


# ------------------------------------------------------------------------ avatar setup


@pytest.mark.skipif(not AVATAR.is_file(), reason=f"no avatar shipped at {AVATAR}")
def test_an_avatar_loads_and_the_scene_reports_it_by_name(empty) -> None:
    assert bridge.scene()["avatars"] == []

    source = (
        "import ApiTypes, import_api\n"
        "option = ApiTypes.ImportExportOption()\n"
        "print(import_api.ImportAvatar({!r}, option))\n".format(AVATAR.as_posix())
    )
    reply = bridge.run_script(source, name="avatar.py")

    assert reply["output"].strip() == "True"
    assert reply["avatars"] == ["genesis_8_female_daz"]


# ---------------------------------------------------------------- drafting and sewing


def test_panels_are_drafted_at_the_millimetre_lengths_they_were_given(empty) -> None:
    bridge.run_script(PANELS, name="panels.py")

    assert bridge.scene()["pattern_count"] == 2
    assert bridge.call("pattern_api.GetLineLength", [0, 0]) == pytest.approx(360.0)
    assert bridge.call("pattern_api.GetLineLength", [0, 1]) == pytest.approx(500.0)


def test_sewing_two_panels_makes_the_seam_groups_md_reports(empty) -> None:
    bridge.run_script(PANELS, name="panels.py")
    assert bridge.scene()["seam_group_count"] == 0

    reply = bridge.run_script(SEW_THE_SIDES, name="sew.py")

    assert reply["seam_group_count"] == 2
    assert bridge.call("pattern_api.GetSeamlinePairGroupListInPattern", [0]) == [0, 1]


@pytest.mark.parametrize(
    "label, args",
    [
        ("one past the end", [0, 4, 1, 3, True, False]),
        ("far out of range", [0, 9, 1, 3, True, False]),
        ("the -1 'all' sentinel", [0, -1, 1, 3, True, False]),
        ("no such pattern piece", [99, 0, 1, 3, True, False]),
    ],
)
def test_a_seam_naming_a_line_md_does_not_have_is_refused_and_says_so(
    empty, label, args
) -> None:
    # Measured on 2026.0.315, and it contradicts what this repo said until now: MD
    # returns False for a bad line or pattern index rather than discarding the call in
    # silence. Every overload does -- segment/segment, segment/innershape and
    # innershape/innershape. Worth a test per case because "it returns False" is only
    # useful if it is false for *all* the ways of getting it wrong.
    bridge.run_script(PANELS, name="panels.py")

    accepted = bridge.call("pattern_api.AddSeamlinePairGroup", args)

    assert accepted is False, label
    assert bridge.scene()["seam_group_count"] == 0, "MD sewed a line it does not have"


def test_a_seam_md_accepts_can_still_be_the_wrong_seam(empty) -> None:
    # The limit of that guard, and the reason the counting discipline stays. MD checks
    # that the indices exist, not that the seam means anything: this joins the front's
    # hem to the back's neckline, which is a garment turned inside out along one edge.
    # It is accepted, counted, and wrong, and neither the return value nor the count
    # can tell you so.
    #
    # Note what is *not* tested here: sewing an edge to itself. MD answers that one
    # with a modal "abnormal seamlines" dialog, which blocks its main thread -- and so
    # the bridge -- until somebody clicks OK. See docs/md-conventions.md.
    bridge.run_script(PANELS, name="panels.py")

    accepted = bridge.call(
        "pattern_api.AddSeamlinePairGroup", [0, 0, 1, 2, True, False]
    )

    assert accepted is True
    assert bridge.scene()["seam_group_count"] == 1


# ------------------------------------------- inner shapes, trims and per-piece physics


def test_inner_shape_indices_are_scoped_to_their_own_piece(empty) -> None:
    # How a dart, a fold line or a pocket placement gets drawn. The index that comes
    # back is per-parent, not scene-global -- the second piece's shapes number from 0
    # again -- which is the easiest of the four index spaces to confuse with the first.
    bridge.run_script(PANELS, name="panels.py")

    fold = bridge.call(
        "pattern_api.CreateInternalShapeWithPoints",
        [0, [(-100.0, 120.0, 0), (100.0, 120.0, 0)], False],
    )
    dart = bridge.call(
        "pattern_api.CreateInternalShapeWithPoints",
        [0, [(-50.0, 300.0, 0), (50.0, 300.0, 0), (0.0, 380.0, 0)], True],
    )
    on_the_back = bridge.call(
        "pattern_api.CreateInternalShapeWithPoints",
        [1, [(300.0, 120.0, 0), (500.0, 120.0, 0)], False],
    )

    assert (fold, dart) == (0, 1)
    assert on_the_back == 0, "inner shape indices are scene-global after all"


def test_a_topstitch_is_added_and_md_counts_it(empty) -> None:
    bridge.run_script(PANELS, name="panels.py")
    styles = bridge.call("pattern_api.GetTopstitchStyleList")
    assert styles[0]["TopstitchStyleName"] == "Default Topstitch"

    added = bridge.call("pattern_api.AddSegmentTopstitch", [0, 0, 0])

    assert added is True
    assert bridge.call("pattern_api.GetPatternAssignedTopstitchCount", [0]) == 1


def test_a_per_piece_simulation_property_round_trips(empty) -> None:
    # Particle distance is the one per-piece physics setting with a getter, so it is the
    # one that can be proven rather than merely accepted. Elastic, shirring and
    # seamtaping are all accepted by this build (returning None) and have no readback at
    # all, so nothing here claims they took effect.
    bridge.run_script(PANELS, name="panels.py")

    bridge.call("pattern_api.SetParticleDistanceOfPattern", [0, 8.0])

    assert bridge.call("pattern_api.GetParticleDistanceOfPattern", [0]) == pytest.approx(
        8.0
    )


def test_simulation_quality_takes_two_numbers_not_one(empty) -> None:
    # GetSimulationQuality() returns a pair, and the setter wants a pair: passing the
    # single number the name suggests is a TypeError, not a quietly ignored call. Worth
    # a test because it is the shape of every overload mistake in this API.
    assert len(bridge.call("utility_api.GetSimulationQuality")) == 2

    with pytest.raises(bridge.BridgeError, match="incompatible function arguments"):
        bridge.call("utility_api.SetSimulationQuality", [1])


# ------------------------------------------------------------------- fabric and colour


def test_a_fabric_is_named_assigned_and_read_back_from_md(empty) -> None:
    bridge.run_script(PANELS, name="panels.py")

    bridge.call("fabric_api.SetFabricName", [0, "md-mcp-jersey"])
    coloured = bridge.call(
        "fabric_api.SetFabricPBRMaterialBaseColor", [0, 0, 0.1, 0.35, 0.7, 1.0]
    )
    bridge.call("pattern_api.SetPatternPieceFabricIndex", [0, 0])

    assert coloured is True
    assert bridge.call("fabric_api.GetFabricName", [0]) == "md-mcp-jersey"
    assert bridge.call("pattern_api.GetPatternPieceFabricIndex", [0]) == 0
    assert bridge.scene()["fabrics"] == ["md-mcp-jersey"]


def test_the_fabric_count_call_cannot_be_used_to_count_fabrics(empty) -> None:
    # Kept as a test rather than a note, because it is the kind of thing a later change
    # "tidies up" into GetFabricCount(). It answers 0 for a scene holding a fabric.
    assert bridge.call("fabric_api.GetFabricStyleNameList") == ["FABRIC 1"]

    assert bridge.call("fabric_api.GetFabricCount") == 0


# ------------------------------------------------------------------------- simulation


def test_one_simulation_step_returns_true_and_does_not_drape(empty, tmp_path) -> None:
    # Simulate(n) runs n solver steps and returns True for any n at all. A caller that
    # reads that True as "the garment is simulated" ships a flat sheet: after one step
    # the panels are still in their 2D plane, to within a millimetre.
    bridge.run_script(PANELS, name="panels.py")
    bridge.run_script(SEW_THE_SIDES, name="sew.py")

    assert bridge.call("utility_api.Simulate", [1]) is True

    measured = export_obj(tmp_path / "one_step.obj")
    assert measured["vertices"] > 0
    assert measured["spans"][2] < 1.0, "one step draped it, so the warning is stale"


def test_enough_simulation_steps_pull_the_sewn_panels_out_of_their_plane(
    empty, tmp_path
) -> None:
    bridge.run_script(PANELS, name="panels.py")
    bridge.run_script(SEW_THE_SIDES, name="sew.py")

    bridge.call("utility_api.Simulate", [400])

    measured = export_obj(tmp_path / "draped.obj")
    # Sewn and dropped, the two panels leave the flat plane entirely. The threshold is
    # far below what was measured (328 mm at 100 steps) -- this is asking whether the
    # solver ran at all, not pinning down a drape.
    assert measured["spans"][2] > 50.0, measured


def test_simulation_steps_continue_from_where_the_last_call_left_off(
    empty, tmp_path
) -> None:
    # Not a fresh solve each time -- which is what makes "simulate a bit more" work, and
    # what makes a loop of Simulate(1) calls quietly expensive.
    #
    # The evidence is that the second 200 steps move the cloth *less* than the first
    # 200. A Simulate that restarted from flat would be deterministic and land in the
    # same place twice, so the mesh would not move at all between them; one that carries
    # on from a partly-settled drape moves it a little and converges. Measured: about
    # 1050 mm over the first 200 steps, 290 mm over the second, 0.3 mm by the eighth.
    bridge.run_script(PANELS, name="panels.py")
    bridge.run_script(SEW_THE_SIDES, name="sew.py")
    flat = export_obj(tmp_path / "flat.obj")

    bridge.call("utility_api.Simulate", [200])
    once = export_obj(tmp_path / "once.obj")
    bridge.call("utility_api.Simulate", [200])
    twice = export_obj(tmp_path / "twice.obj")

    first_move = furthest_move(flat, once)
    second_move = furthest_move(once, twice)

    assert second_move > 1.0, "the second call did nothing at all"
    assert second_move < first_move, (first_move, second_move)


# Deliberately not tested: how the drape settles. With no arrangement point to hang
# from, the cloth free-falls and tumbles, so vertex positions past the first few hundred
# steps are chaotic -- a threshold that passes here fails on the next run for reasons
# that have nothing to do with the bridge. What md-mcp is answerable for is that it can
# drive the solver and read the result back, which the two tests above pin down.
# docs/md-conventions.md records the settling numbers as measurements, not as a contract.


# ------------------------------------------------------------------------------ export


def test_a_simulated_garment_exports_a_mesh_with_both_pieces_in_it(
    empty, tmp_path
) -> None:
    bridge.run_script(PANELS, name="panels.py")
    bridge.run_script(SEW_THE_SIDES, name="sew.py")
    bridge.call("utility_api.Simulate", [200])

    target = tmp_path / "garment.obj"
    measured = export_obj(target)

    assert measured["vertices"] > 100
    body = target.read_text(encoding="utf-8", errors="replace")
    assert "g front" in body and "g back" in body
    # MD writes the material library alongside, and names it after the target.
    assert target.with_suffix(".mtl").is_file()


def test_a_project_survives_being_saved_and_reopened(empty, tmp_path) -> None:
    # The persistence path, and the one that replaces the whole scene rather than
    # adding to it. Everything named here -- pieces, their names, the seams, the
    # renamed fabric -- has to come back, and it is read out of the reopened scene
    # rather than out of what was sent.
    bridge.run_script(PANELS, name="panels.py")
    bridge.run_script(SEW_THE_SIDES, name="sew.py")
    bridge.call("fabric_api.SetFabricName", [0, "md-mcp-jersey"])

    project = tmp_path / "md_mcp_round_trip.zprj"
    written = bridge.call("export_api.ExportZPrjW", [project.as_posix(), True])
    assert project.is_file(), written

    bridge.call("utility_api.NewProject")
    assert bridge.scene()["pattern_count"] == 0

    reopened = bridge.run_script(
        "import ApiTypes, import_api\n"
        "option = ApiTypes.ImportZPRJOption()\n"
        "print(import_api.ImportZprjW({!r}, option))\n".format(project.as_posix()),
        name="reopen.py",
    )

    assert reopened["output"].strip() == "True"
    assert [piece["name"] for piece in reopened["pieces"]] == ["front", "back"]
    assert reopened["seam_group_count"] == 2
    assert reopened["fabrics"] == ["md-mcp-jersey"]


def test_a_pattern_and_its_sewing_round_trip_through_pattern_json(
    empty, tmp_path
) -> None:
    # The other way to add a sewing pattern: author the whole document rather than
    # drafting it call by call. Import *replaces* the scene, so this proves the pieces
    # and the seams both come back, not just the shapes.
    bridge.run_script(PANELS, name="panels.py")
    bridge.run_script(SEW_THE_SIDES, name="sew.py")

    document = tmp_path / "pattern.json"
    assert bridge.call("pattern_api.ExportPatternJSON", [document.as_posix()]) is True

    bridge.call("utility_api.NewProject")
    assert bridge.scene()["pattern_count"] == 0

    assert bridge.call("pattern_api.ImportPatternJSON", [document.as_posix()]) is True

    scene = bridge.scene()
    assert [piece["name"] for piece in scene["pieces"]] == ["front", "back"]
    assert scene["seam_group_count"] == 2
    assert bridge.call("pattern_api.GetLineLength", [0, 0]) == pytest.approx(360.0)


def test_pattern_json_expresses_a_seam_as_an_arc_length_span(empty, tmp_path) -> None:
    # Why this route is worth knowing about: a seam is a fraction of a piece's outline,
    # referred to by shape id, and there is no line index anywhere in it. The whole
    # class of failure the line-index rules exist to avoid cannot be written down here.
    bridge.run_script(PANELS, name="panels.py")
    bridge.run_script(SEW_THE_SIDES, name="sew.py")

    document = tmp_path / "spans.json"
    bridge.call("pattern_api.ExportPatternJSON", [document.as_posix()])
    written = json.loads(document.read_text(encoding="utf-8"))

    assert written["Unit"] == "mm"
    pair = written["SeamLinePairGroupList"][0]["PairList"][0]
    assert set(pair["First"]) == {"ShapeID", "LengthParam", "Direction"}
    assert set(pair["First"]["LengthParam"]) == {"fStart", "fEnd"}
    assert isinstance(pair["First"]["Direction"], bool)


def test_pattern_json_carries_what_the_call_api_has_no_calls_for(
    empty, tmp_path
) -> None:
    # Notches, seam allowances and button placement have no setter anywhere in the six
    # modules, but the document has a place for all of them -- so authoring the JSON is
    # the only way to reach them. Empty here because nothing above adds any; what is
    # being pinned down is that the fields exist to be filled in.
    bridge.run_script(PANELS, name="panels.py")

    document = tmp_path / "fields.json"
    bridge.call("pattern_api.ExportPatternJSON", [document.as_posix()])
    piece = json.loads(document.read_text(encoding="utf-8"))["PatternList"][0]

    assert {
        "NotchList",
        "OuterSeamAllowanceList",
        "ButtonHeadList",
        "ButtonHoleList",
        "InternalLineList",
    } <= set(piece)


def test_an_export_to_a_path_that_cannot_be_written_reports_rather_than_hangs(
    empty,
) -> None:
    bridge.run_script(PANELS, name="panels.py")

    source = (
        "import ApiTypes, export_api\n"
        "option = ApiTypes.ImportExportOption()\n"
        "print(export_api.ExportOBJ('Z:/nowhere/md-mcp.obj', option))\n"
    )
    reply = bridge.run_script(source, name="bad_export.py")

    # Whatever MD does about the path, the bridge must come back and keep serving --
    # this is the everyday failure, and it must not look like a dead listener.
    assert reply["pattern_count"] == 2
    assert bridge.ping()["ok"]
