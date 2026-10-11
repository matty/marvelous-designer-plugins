"""The read-back tools and the export check, against a real Marvelous Designer.

``tests/test_readback_tools.py`` proves the scripts against a fake MD, which proves
nothing about MD. These run the same scripts in MD and check what came back against
what was built. Run with MD open and the listener started::

    .venv\\Scripts\\python.exe -m pytest -m md_required tests/md_required/test_readback_live.py

**The ``empty`` tests call ``NewProject``, which discards the open scene.** The ``md``
fixture refuses to run them against a scene with anything in it.
"""

from __future__ import annotations

import hashlib
import os
import pathlib

import pytest

from md_mcp import bridge

pytestmark = pytest.mark.md_required

#: Ships with MD, so these do not depend on anyone's own files.
AVATAR = pathlib.Path(
    "C:/Program Files/Marvelous Designer Enterprise Network Offline"
    "/Preset/Avatar/Converting/Female/Genesis8_Female.avt"
)

#: An avatar *with* arrangement points: the shipped Genesis avatars load with none
#: (docs/md-conventions.md). Set MD_MCP_ARRANGEMENT_AVATAR to one; the default is the
#: Nardo avatar these points were first measured on, where it exists.
ARRANGEMENT_AVATAR = pathlib.Path(
    os.environ.get(
        "MD_MCP_ARRANGEMENT_AVATAR",
        os.path.expanduser("~/Documents/3D Models/Nardo/Nardo.avt"),
    )
)

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def empty(md):
    """A scene with nothing in it, proven rather than assumed."""
    bridge.call("utility_api.NewProject")
    assert bridge.scene()["pattern_count"] == 0
    return md


def load_avatar(path: pathlib.Path) -> None:
    source = (
        "import ApiTypes, import_api\n"
        "print(import_api.ImportAvatar({!r}, ApiTypes.ImportExportOption()))\n".format(
            path.as_posix()
        )
    )
    assert bridge.run_script(source, name="avatar.py")["output"].strip() == "True"


SQUARE = """
import pattern_api
index = pattern_api.CreatePatternWithPoints(
    [(0.0, 0.0, 0), (300.0, 0.0, 0), (300.0, 300.0, 0), (0.0, 300.0, 0)]
)
pattern_api.SetPatternPieceName(index, "square")
"""


# ------------------------------------------------------------------------- md_measure


@pytest.mark.skipif(not AVATAR.is_file(), reason=f"no avatar shipped at {AVATAR}")
def test_measure_reads_the_avatar_and_a_piece_in_millimetres(empty) -> None:
    load_avatar(AVATAR)
    bridge.run_script(SQUARE, name="square.py")

    measured = bridge.measure(heights=[1000.0])

    height = measured["avatar"]["size"][1]
    assert 1400 < height < 2100, f"an adult avatar should be 1.4-2.1 m, got {height} mm"
    groups = measured["garment"]["groups"]
    assert [g["name"] for g in groups] == ["square"], groups
    width = groups[0]["max"][0] - groups[0]["min"][0]
    tall = groups[0]["max"][1] - groups[0]["min"][1]
    # Flat and unsimulated, so the cloth is the drafted square, give or take the mesh.
    assert abs(width - 300) < 15 and abs(tall - 300) < 15, groups[0]
    assert measured["cross_sections"][0]["vertices"] > 0


# ------------------------------------------------------------------------ md_snapshot


@pytest.mark.skipif(not AVATAR.is_file(), reason=f"no avatar shipped at {AVATAR}")
def test_front_side_and_back_are_three_different_pictures(empty) -> None:
    # 8 is the back (measured 2026-10-11). No cloth, so the bytes are stable for a
    # fixed view and a difference means the camera moved.
    load_avatar(AVATAR)

    shots = bridge.snapshots([2, 4, 8])

    assert [meta["view"] for _, meta in shots] == [2, 4, 8]
    assert all(data.startswith(PNG) and meta["camera_moved"] for data, meta in shots)
    digests = {hashlib.sha256(data).digest() for data, _ in shots}
    assert len(digests) == 3, "front, side and back came back as the same picture"


# ------------------------------------------------------------------ md_fabric_presets


def test_fabric_presets_finds_files_that_exist(md_live) -> None:
    found = bridge.fabric_presets()

    assert found["count"] > 0, found["roots"]
    paths = [f["path"] for folder in found["folders"] for f in folder["fabrics"]]
    assert all(p.lower().endswith(".zfab") for p in paths)
    missing = [p for p in paths if not os.path.isfile(p)]
    assert not missing, missing[:5]


# ------------------------------------------------------------- md_arrangement_points


@pytest.mark.skipif(
    not ARRANGEMENT_AVATAR.is_file(),
    reason=f"no avatar with arrangement points at {ARRANGEMENT_AVATAR} "
    "(set MD_MCP_ARRANGEMENT_AVATAR)",
)
def test_arrangement_points_are_resolved_and_the_probe_is_removed(empty) -> None:
    load_avatar(ARRANGEMENT_AVATAR)
    bridge.run_script(SQUARE, name="square.py")

    resolved = bridge.arrangement_points(["Neck_Back", "Shoulder_L"])

    by_name = {p["name"]: p for p in resolved["points"]}
    assert sorted(by_name) == ["Neck_Back", "Shoulder_L"], resolved.get("failed")
    # Neck_Back sits above Shoulder_L on any upright avatar.
    assert by_name["Neck_Back"]["center"][1] > by_name["Shoulder_L"]["center"][1]
    probe = resolved["probe"]
    assert probe["unchanged"], probe
    assert [p["name"] for p in bridge.scene()["pieces"]] == ["square"]


# ---------------------------------------------------------------------- write checks


def test_an_export_into_a_missing_folder_is_created_and_checked(md_live, tmp_path) -> None:
    # Measured: ExportOBJ into a missing folder returned its paths and wrote nothing.
    # The check creates the folder first and reports what landed.
    target = tmp_path / "not-yet" / "shot.png"
    reply = bridge.run_script(
        "import export_api\nexport_api.ExportSnapshot3D({!r})\n".format(str(target)),
        name="export.py",
    )

    records = [w for w in reply["writes"] if w["call"] == "export_api.ExportSnapshot3D"]
    assert records, reply["writes"]
    assert records[0].get("created_folders") == [str(target.parent)]
    assert not records[0].get("missing_outputs"), records[0]
    assert target.is_file()


# ------------------------------------------------------------------------- md_seams


PANELS = """
import pattern_api
for name, x in (("front", -180.0), ("back", 220.0)):
    index = pattern_api.CreatePatternWithPoints(
        [(x, 0.0, 0), (x + 360.0, 0.0, 0), (x + 360.0, 500.0, 0), (x, 500.0, 0)])
    pattern_api.SetPatternPieceName(index, name)
"""


def test_seams_reads_back_which_lines_were_joined(empty) -> None:
    # The same pair as test_garment_live's side seams: front's right edge (line 1) to
    # back's left (line 3), and the other way round.
    bridge.run_script(PANELS, name="panels.py")
    bridge.call("pattern_api.AddSeamlinePairGroup", [0, 1, 1, 3, True, False])
    bridge.call("pattern_api.AddSeamlinePairGroup", [0, 3, 1, 1, True, False])

    from md_mcp import seams

    ledger = seams.ledger(bridge.seams())

    joined = sorted(
        ((pair["a"]["piece"], tuple(pair["a"]["lines"])),
         (pair["b"]["piece"], tuple(pair["b"]["lines"])))
        for group in ledger["groups"] for pair in group["pairs"]
    )
    assert ledger["group_count"] == 2
    assert joined == sorted([
        (("front", (1,)), ("back", (3,))),
        (("front", (3,)), ("back", (1,))),
    ]), ledger
    assert all(length == 500.0 for group in ledger["groups"]
               for pair in group["pairs"] for length in pair["length_mm"])
    # Nothing here is sewn twice or to itself, and MD's lengths match the document's.
    assert ledger["warnings"] == {}, ledger["warnings"]
