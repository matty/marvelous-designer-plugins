"""md_measure, md_arrangement_points, md_snapshot and md_fabric_presets, offline.

Each is a script md-mcp sends through the ``run`` op, so here the shipped listener runs it
against the fake host: the fake writes real OBJ and PNG files from a scene it keeps, and
the tools have to read them back. That proves the parsing, the clean-up, the probe's
bookkeeping and the protocol. It proves nothing about Marvelous Designer -- what MD's
OBJ groups are called, where it really puts an arranged piece, what its snapshot looks
like -- beyond the measurements the fake was built from.
"""

from __future__ import annotations

import ast
import base64
import json
import math
import os
import sys

import pytest

from md_mcp import bridge, server as md_server
from md_mcp.server import build_server
from tests.fake_md_host import png_bytes, running_listener
from tests.test_server import call, error_text, payload


@pytest.fixture
def md(monkeypatch):
    with running_listener(monkeypatch) as host:
        yield host


@pytest.fixture
def server():
    return build_server()


def body(host, tail: bool = True) -> None:
    """A crude avatar: rings every 50 mm, wider at the torso, and a long tail behind.

    Rings at exact quarter turns make the extremes exact: x = +/-a, z = +/-b.
    """
    host.avatars = ["Nardo"]
    vertices = []
    for height in range(0, 1801, 50):
        torso = 900 <= height <= 1500
        half_width, half_depth = (180, 120) if torso else (100, 80)
        for step in range(12):
            angle = math.radians(step * 30)
            vertices.append(
                (half_width * math.cos(angle), height, half_depth * math.sin(angle))
            )
    if tail:
        vertices += [(0.0, 1000.0, -100.0 - 100 * k) for k in range(11)]
    host.avatar_vertices = vertices


BUILD = """
for name, x in {pieces!r}:
    index = pattern_api.CreatePatternWithPoints(
        [(x, 0, 0), (x + 300, 0, 0), (x + 300, 400, 0), (x, 400, 0)])
    pattern_api.SetPatternPieceName(index, name)
"""


def build(pieces: list[tuple[str, float]]) -> None:
    bridge.run_script(BUILD.format(pieces=pieces))


# ------------------------------------------------------------------- the OBJ reader


def obj_reader():
    namespace: dict = {}
    exec(compile(bridge._OBJ_HELPERS, "<obj helpers>", "exec"), namespace)
    return namespace["_mdmcp_read_obj"]


def test_a_group_is_the_vertices_its_faces_use_wherever_they_are_listed(
    tmp_path,
) -> None:
    # Every vertex first, groups after: a group statement does not own the vertices
    # listed after it, its faces do. Negative and v/vt/vn indices both occur in OBJ.
    path = tmp_path / "late_groups.obj"
    path.write_text(
        "v 0 0 0\nv 10 0 0\nv 0 10 0\n"
        "v 100 100 100\nv 110 100 100\nv 100 110 100\n"
        "g left\nf 1/1/1 2/2/2 3/3/3\n"
        "g right\nf -3//1 -2//1 -1//1\n",
        encoding="utf-8",
    )

    positions, groups = obj_reader()(str(path), "garment")

    assert len(positions) == 6
    assert [(g["name"], len(g["points"])) for g in groups] == [("left", 3), ("right", 3)]
    assert max(p[0] for p in groups[1]["points"]) == 110


def test_a_file_with_no_faces_falls_back_to_the_vertices_under_each_group(
    tmp_path,
) -> None:
    path = tmp_path / "points.obj"
    path.write_text("v 1 1 1\ng a\nv 2 2 2\nv 3 3 3\ng b\nv 4 4 4\n", encoding="utf-8")

    _, groups = obj_reader()(str(path), "garment")

    assert [(g["name"], len(g["points"])) for g in groups] == [
        ("garment", 1), ("a", 2), ("b", 1)
    ]


def test_an_object_line_before_a_group_line_is_not_a_second_group(tmp_path) -> None:
    path = tmp_path / "both.obj"
    path.write_text(
        "o Mesh\ng Mesh\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8"
    )

    _, groups = obj_reader()(str(path), "avatar")

    assert [(g["name"], g["occurrence"]) for g in groups] == [("Mesh", 1)]


def test_snippets_parse_as_the_python_md_embeds() -> None:
    # They run inside MD's CPython 3.11.8, not this interpreter.
    for source in (bridge._MEASURE, bridge._ARRANGEMENT, bridge._SNAPSHOT,
                   bridge._FABRIC_PRESETS, bridge._SEAMS):
        script = source.replace(bridge._PARAMS, repr(json.dumps({})))
        ast.parse(script, feature_version=(3, 11))


# -------------------------------------------------------------------------- measure


def test_measure_reports_the_avatar_and_slices_through_it(md, server) -> None:
    body(md)

    result = payload(call(server, "md_measure", {"heights": [1200, 1000, 1025]}))

    avatar = result["avatar"]
    assert avatar["names"] == ["Nardo"]
    assert avatar["min"] == [-180.0, 0.0, -1100.0]
    assert avatar["max"] == [180.0, 1800.0, 120.0]
    assert avatar["groups"] == ["Mesh"]
    torso, tail, between = result["cross_sections"]["sections"]
    assert torso == {"height": 1200, "vertices": 12, "x": [-180.0, 180.0],
                     "z": [-120.0, 120.0]}
    assert tail["z"] == [-1100.0, 120.0], "the tail is in the slice it crosses"
    assert between == {"height": 1025, "vertices": 0}
    assert result["cross_sections"]["band_mm"] == 20.0
    # Only the avatar was asked for: with no pieces, the garment is not exported.
    assert result["garment"] is None
    assert [c for c in md.calls if c[0] == "ExportOBJ"] == [
        ("ExportOBJ", md.written[0], True, False)
    ]


def test_a_wider_band_takes_in_more_of_the_avatar(md, server) -> None:
    body(md, tail=False)

    narrow = payload(call(server, "md_measure", {"heights": [1025]}))
    wide = payload(call(server, "md_measure", {"heights": [1025], "band_mm": 60}))

    assert narrow["cross_sections"]["sections"][0]["vertices"] == 0
    assert wide["cross_sections"]["sections"][0]["vertices"] == 24


def test_measure_lists_each_garment_group(md, server) -> None:
    build([("front", 0), ("back", 400)])

    garment = payload(call(server, "md_measure"))["garment"]

    assert garment["min"] == [0.0, 0.0, 0.0]
    assert garment["max"] == [700.0, 400.0, 0.0]
    assert garment["groups_total"] == 2
    front, back = garment["groups"]
    assert front == {"name": "front", "vertices": 4, "min": [0.0, 0.0, 0.0],
                     "max": [300.0, 400.0, 0.0], "centroid": [150.0, 200.0, 0.0]}
    assert back["name"] == "back" and back["centroid"] == [550.0, 200.0, 0.0]


def test_a_repeated_group_name_is_listed_twice_not_merged(md, server) -> None:
    build([("sleeve", 0), ("sleeve", 400)])

    garment = payload(call(server, "md_measure"))["garment"]

    assert [(g["name"], g.get("occurrence", 1)) for g in garment["groups"]] == [
        ("sleeve", 1), ("sleeve", 2)
    ]
    assert garment["repeated_group_names"] == ["sleeve"]


def test_an_export_with_no_groups_is_one_group_called_garment(md, server) -> None:
    md.obj_groups = "none"
    build([("front", 0), ("back", 400)])

    garment = payload(call(server, "md_measure"))["garment"]

    assert [g["name"] for g in garment["groups"]] == ["garment"]
    assert garment["groups"][0]["vertices"] == 8


def test_groups_can_be_searched_and_capped(md, server) -> None:
    build([("sleeve left", 0), ("front", 400), ("sleeve right", 800)])

    searched = payload(call(server, "md_measure", {"group_search": "SLEEVE"}))["garment"]
    capped = payload(call(server, "md_measure", {"max_groups": 1}))["garment"]

    assert [g["name"] for g in searched["groups"]] == ["sleeve left", "sleeve right"]
    assert searched["groups_matching"] == 2 and searched["groups_total"] == 3
    assert [g["name"] for g in capped["groups"]] == ["sleeve left"]
    assert capped["groups_omitted"] == 2


def test_measure_leaves_no_files_behind(md, server) -> None:
    body(md)
    build([("front", 0)])

    result = payload(call(server, "md_measure", {"heights": [1200]}))

    assert len(md.written) == 4, "an avatar and a garment export, each with an .mtl"
    assert not [path for path in md.written if os.path.exists(path)]
    assert not os.path.exists(os.path.dirname(md.written[0]))
    assert "cleanup_incomplete" not in result


def test_an_empty_scene_is_measured_as_empty_without_exporting(md, server) -> None:
    result = payload(call(server, "md_measure", {"heights": [1000]}))

    assert result["avatar"] is None and "no avatar" in result["avatar_note"]
    assert result["garment"] is None and "no pattern pieces" in result["garment_note"]
    assert result["cross_sections"] is None
    assert not [c for c in md.calls if c[0] == "ExportOBJ"]


def test_an_export_md_did_not_make_fails_the_measurement(md, server) -> None:
    # Measured: ExportOBJ can return its path list and write nothing. A measurement made
    # from a file that is not there must fail, not report an empty avatar.
    body(md)
    md.exports_write = False

    message = error_text(call(server, "md_measure"))

    assert "wrote no .obj" in message


def test_a_nonsense_band_is_refused(server) -> None:
    assert "band_mm" in error_text(call(server, "md_measure", {"band_mm": 0}))


# ------------------------------------------------------------- arrangement points


POINTS = [
    ("Body_Front_Center_1", (0.0, 1300.0, 150.0)),
    ("Body_Back_Center_1", (0.0, 1300.0, -1150.0)),
    ("Arm_Back_1_L", (450.0, 1350.0, -50.0)),
]


@pytest.fixture
def avatar(md):
    body(md)
    md.arrangement_points = list(POINTS)
    return md


def test_every_point_is_resolved_to_where_a_probe_lands(avatar, server) -> None:
    result = payload(call(server, "md_arrangement_points"))

    assert result["available"] == 3
    assert [(p["name"], p["center"]) for p in result["points"]] == [
        (name, list(position)) for name, position in POINTS
    ]
    back = result["points"][1]
    assert back["min"] == [-100.0, 1230.0, -1150.0]
    assert back["max"] == [100.0, 1370.0, -1150.0]
    probe = result["probe"]
    assert probe["unchanged"] is True
    assert probe["pattern_count_before"] == probe["pattern_count_after"] == 0
    assert probe["pattern_count_read_back"] == 0
    assert "temporary pattern piece" in probe["what"]
    assert avatar.piece_names == []
    assert [c for c in avatar.calls if c[0] == "DeletePatternPiece"] == [
        ("DeletePatternPiece", 0)
    ]


def test_the_scene_keeps_its_pieces_and_their_numbers(avatar, server) -> None:
    build([("front", 0), ("back", 400)])
    before = (list(avatar.piece_names), list(avatar.piece_points))

    result = payload(call(server, "md_arrangement_points", {"points": ["Arm_Back_1_L"]}))

    assert result["points"][0]["center"] == [450.0, 1350.0, -50.0]
    assert result["probe"]["deleted_index"] == 2
    assert result["probe"]["found_in_export_by"] == "its group name"
    assert result["probe"]["seam_group_count_before"] == \
        result["probe"]["seam_group_count_after"]
    assert result["probe"]["fabrics_unchanged"] is True
    assert (avatar.piece_names, avatar.piece_points) == before
    assert not [path for path in avatar.written if os.path.exists(path)]


def test_a_probe_is_found_even_when_groups_are_not_named_after_pieces(
    avatar, server
) -> None:
    # Unmeasured how MD names groups in every case; the measured case is piece names. If
    # they were generic, the probe is the group the export gained when it was added.
    avatar.obj_groups = "generic"
    build([("front", 0)])

    result = payload(call(server, "md_arrangement_points", {"points": [1]}))

    assert result["points"][0]["center"] == [0.0, 1300.0, -1150.0]
    assert result["probe"]["found_in_export_by"].startswith("the group the export gained")


def test_a_probe_that_cannot_be_told_apart_fails_its_points_and_still_goes(
    avatar, server
) -> None:
    avatar.obj_groups = "none"
    build([("front", 0)])

    result = payload(call(server, "md_arrangement_points"))

    assert result["points"] == []
    assert len(result["failed"]) == 3
    assert "could not tell the probe" in result["failed"][0]["error"]
    assert result["probe"]["unchanged"] is True
    assert avatar.piece_names == ["front"]


def test_points_can_be_named_in_part_or_by_index(avatar, server) -> None:
    result = payload(call(server, "md_arrangement_points",
                          {"points": ["Back", 0, "Nowhere", 9]}))

    assert [p["index"] for p in result["points"]] == [1, 2, 0]
    assert result["unmatched"] == ["Nowhere", 9]


def test_an_arrangement_list_of_plain_names_is_read_too(avatar, server) -> None:
    avatar.arrangement_list_format = "str"

    result = payload(call(server, "md_arrangement_points", {"points": ["Body_Back"]}))

    assert [p["name"] for p in result["points"]] == ["Body_Back_Center_1"]


def test_the_probe_is_deleted_when_exports_start_failing(
    avatar, server, monkeypatch
) -> None:
    exporter = sys.modules["export_api"]
    real = exporter.ExportOBJ
    made = []

    def flaky(path, option):
        made.append(path)
        if len(made) > 1:
            raise RuntimeError("disk full")
        return real(path, option)

    monkeypatch.setattr(exporter, "ExportOBJ", flaky)

    result = payload(call(server, "md_arrangement_points"))

    assert [p["name"] for p in result["points"]] == ["Body_Front_Center_1"]
    assert [f["error"] for f in result["failed"]] == ["RuntimeError: disk full"] * 2
    assert result["probe"]["unchanged"] is True
    assert avatar.piece_names == []


def test_a_scene_the_probe_cannot_account_for_is_reported_and_left_alone(
    avatar, server, monkeypatch
) -> None:
    # If the count does not rise by exactly one, deleting "the last piece" could delete
    # somebody's work. Nothing is deleted, and the tool fails saying so.
    patterns = sys.modules["pattern_api"]
    real = patterns.CreatePatternWithPoints

    def twice(points):
        real(points)
        return real(points)

    monkeypatch.setattr(patterns, "CreatePatternWithPoints", twice)

    message = error_text(call(server, "md_arrangement_points"))

    assert "pattern count went from 0 to 2" in message
    assert "nothing was deleted" in message
    assert '"unchanged": false' in message
    assert len(avatar.piece_names) == 2
    assert not [c for c in avatar.calls if c[0] == "DeletePatternPiece"]


def test_a_probe_index_that_is_not_the_last_is_never_touched(
    avatar, server, monkeypatch
) -> None:
    # The new piece goes last. If MD ever answered another index, naming, arranging or
    # deleting it would edit somebody's piece -- so nothing is done to it, and the
    # stray probe is reported rather than guessed at.
    build([("front", 0)])
    patterns = sys.modules["pattern_api"]
    real = patterns.CreatePatternWithPoints

    def wrong_index(points):
        real(points)
        return 0

    monkeypatch.setattr(patterns, "CreatePatternWithPoints", wrong_index)

    message = error_text(call(server, "md_arrangement_points"))

    assert "not 1, the last one" in message
    assert "nothing was deleted" in message
    assert avatar.piece_names[0] == "front"
    assert avatar.placements[0] is None
    assert not [c for c in avatar.calls if c[0] in ("DeletePatternPiece", "SetArrangement")]


def test_an_unnamed_probe_is_still_removed_by_index(avatar, server, monkeypatch) -> None:
    def refuse(index, name):
        raise RuntimeError("names are read-only today")

    monkeypatch.setattr(sys.modules["pattern_api"], "SetPatternPieceName", refuse)

    result = payload(call(server, "md_arrangement_points", {"points": [0]}))

    assert result["probe"]["unchanged"] is True
    assert result["probe"]["deleted_index"] == 0


def test_no_points_means_no_probe(md, server) -> None:
    result = payload(call(server, "md_arrangement_points"))

    assert result["points"] == []
    assert result["probe"]["used"] is False
    assert "GetArrangementList() is empty" in result["probe"]["why"]
    assert not [c for c in md.calls if c[0] == "CreatePatternWithPoints"]


# ------------------------------------------------------------------------- snapshot


def image_of(result) -> tuple[dict, bytes]:
    assert not result.is_error, result.content[0].text
    meta = json.loads(result.content[0].text)
    image = result.content[1]
    assert image.type == "image" and image.mime_type == "image/png"
    return meta, base64.b64decode(image.data)


def test_a_snapshot_comes_back_as_an_image_and_moves_the_camera(md, server) -> None:
    meta, data = image_of(call(server, "md_snapshot", {"view": 3, "max_size": 0}))

    assert data == png_bytes(640, 480)
    assert meta["view"] == 3
    assert meta["facing"] == "three-quarter left"
    assert meta["size"] == [640, 480]
    assert "left there" in meta["camera"]
    names = [c[0] for c in md.calls]
    assert names.index("SetCamViewPoint") < names.index("Refresh3DWindow") \
        < names.index("ExportSnapshot3D")
    assert md.camera == 3
    assert not [path for path in md.written if os.path.exists(path)]


def test_a_snapshot_without_a_view_leaves_the_camera_alone(md, server) -> None:
    meta, _ = image_of(call(server, "md_snapshot", {"max_size": 0}))

    assert "not moved" in meta["camera"]
    assert not [c for c in md.calls if c[0] == "SetCamViewPoint"]


def test_the_back_view_is_reachable(md, server) -> None:
    # Measured 2026-10-11 on 2026.0.315: SetCamViewPoint(8) is the back, which an
    # earlier 0-7 range refused.
    meta, _ = image_of(call(server, "md_snapshot", {"view": 8, "max_size": 0}))

    assert meta["facing"] == "back"
    assert md.camera == 8


def test_a_view_md_does_not_have_is_refused_before_md_is_asked(md, server) -> None:
    assert "0 to 9" in error_text(call(server, "md_snapshot", {"view": 10}))
    assert "0 to 9" in error_text(call(server, "md_snapshot", {"view": -1}))
    assert not md.calls


@pytest.mark.skipif(sys.platform != "win32", reason="scaling uses Windows GDI+")
def test_a_snapshot_is_scaled_to_max_size(md, server) -> None:
    md.snapshot_size = (1600, 900)

    meta, data = image_of(call(server, "md_snapshot", {"max_size": 400}))

    assert meta["full_size"] == [1600, 900]
    assert meta["size"] == [400, 225]
    assert md_server._png_size(data) == (400, 225)
    assert meta["bytes"] == len(data)


def test_a_snapshot_that_cannot_be_scaled_is_sent_whole_and_says_so(
    md, server, monkeypatch
) -> None:
    md.snapshot_size = (1600, 900)

    def no_scaler(data, max_size):
        raise OSError("no GDI+ here")

    monkeypatch.setattr(md_server, "_shrink_png", no_scaler)

    meta, data = image_of(call(server, "md_snapshot"))

    assert meta["size"] == [1600, 900]
    assert "no GDI+ here" in meta["scaling_error"]
    assert data == png_bytes(1600, 900)


def test_a_snapshot_md_did_not_write_is_an_error(md, server) -> None:
    md.exports_write = False

    assert "wrote nothing" in error_text(call(server, "md_snapshot"))


# ----------------------------------------------------------------- fabric presets


def test_presets_are_found_beside_md_and_in_public_documents(
    md, server, monkeypatch, tmp_path
) -> None:
    install = tmp_path / "Marvelous Designer"
    shipped = install / "Preset" / "Fabric" / "Cotton"
    shipped.mkdir(parents=True)
    (shipped / "Cotton_Oxford.zfab").write_bytes(b"zfab")
    (shipped / "readme.txt").write_text("not a fabric")
    public = tmp_path / "Public"
    assets = public / "Documents" / "MarvelousDesigner" / "New Assets" / "Fabric"
    assets.mkdir(parents=True)
    (assets / "Denim_Heavy.ZFAB").write_bytes(b"zfab")
    # Inside MD this is MD's own executable; the install is the folder it is in.
    monkeypatch.setattr(sys, "executable", str(install / "MarvelousDesigner.exe"))
    monkeypatch.setenv("PUBLIC", str(public))

    result = payload(call(server, "md_fabric_presets"))

    folders = {os.path.normcase(f["folder"]): f for f in result["folders"]}
    ours = folders[os.path.normcase(str(shipped))]
    assert ours["source"] == "md install"
    assert ours["fabrics"] == [
        {"name": "Cotton_Oxford", "path": str(shipped / "Cotton_Oxford.zfab")}
    ]
    theirs = folders[os.path.normcase(str(assets))]
    assert theirs["fabrics"][0]["name"] == "Denim_Heavy"
    assert theirs["source"] == "public documents"
    roots = {os.path.normcase(r["root"]): r for r in result["roots"]}
    assert roots[os.path.normcase(str(install / "Preset"))]["zfab_files"] == 1
    assert result["md_executable"] == str(install / "MarvelousDesigner.exe")

    denim = payload(call(server, "md_fabric_presets", {"search": "denim HEAVY"}))
    paths = [f["path"] for folder in denim["folders"] for f in folder["fabrics"]]
    assert str(assets / "Denim_Heavy.ZFAB") in paths
    assert str(shipped / "Cotton_Oxford.zfab") not in paths
    assert denim["count"] == len(paths)
