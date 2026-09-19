"""End to end against a real Marvelous Designer.

Run MD with ``plugin/md_mcp_listener.py`` started in it, then::

    .venv\\Scripts\\python.exe -m pytest -m md_required

These are the claims a fake host cannot make: that MD accepts what the bridge sends,
that its API is spelled the way the search says, and that a script's effects are visible
to the next call.

**They build into the open scene and call ``NewProject``, which discards it.** There is
no undo in the MD API and nothing here restores anything, so the fixture refuses to run
unless the scene is empty. Save your work before running them.
"""

from __future__ import annotations

import json
import time

import pytest

from md_mcp import bridge

pytestmark = pytest.mark.md_required

SQUARE = """
import pattern_api, utility_api
utility_api.NewProject()
index = pattern_api.CreatePatternWithPoints(
    [(0.0, 0.0, 0), (300.0, 0.0, 0), (300.0, 300.0, 0), (0.0, 300.0, 0)]
)
pattern_api.SetPatternPieceName(index, "md-mcp-square")
print(index)
"""


def test_md_answers_and_says_which_build_it_is(md) -> None:
    assert md["md_version"].count(".") == 2, md


def test_the_api_search_reads_the_live_build(md) -> None:
    found = {entry["call"] for entry in bridge.api("PatternCount")}

    # The call md-mcp leans on everywhere. GetPatternSize looks like its twin in the
    # published docs and is not: it returned 0 for a three-piece scene.
    assert "pattern_api.GetPatternCount" in found


def test_the_search_covers_more_than_one_module(md) -> None:
    modules = {entry["call"].split(".")[0] for entry in bridge.api("")}

    assert {"pattern_api", "utility_api"} <= modules
    assert len(bridge.api("")) > 100, "the whole API is a few hundred calls"


def test_a_call_reaches_md_and_its_effect_is_visible_to_the_next_one(md) -> None:
    bridge.call("utility_api.NewProject")
    assert bridge.call("pattern_api.GetPatternCount") == 0

    index = bridge.call(
        "pattern_api.CreatePatternWithPoints",
        [[(0.0, 0.0, 0), (200.0, 0.0, 0), (200.0, 200.0, 0), (0.0, 200.0, 0)]],
    )
    bridge.call("pattern_api.SetPatternPieceName", [index, "md-mcp-call"])

    assert bridge.call("pattern_api.GetPatternCount") == 1
    assert bridge.call("pattern_api.GetPatternPieceName", [index]) == "md-mcp-call"


def test_a_script_builds_and_the_scene_comes_back_read_from_md(md) -> None:
    reply = bridge.run_script(SQUARE, name="square.py")

    assert reply["output"].strip() == "0"
    assert [piece["name"] for piece in reply["pieces"]] == ["md-mcp-square"]
    assert bridge.scene()["pieces"] == reply["pieces"]


def test_lengths_come_back_in_millimetres_whatever_the_ui_shows(md) -> None:
    bridge.run_script(SQUARE, name="square.py")

    # A 300 mm side, drawn as 300 units. Reading it as centimetres would make it 3 m.
    assert bridge.call("pattern_api.GetLineLength", [0, 0]) == pytest.approx(300.0)


def test_a_failing_script_reports_its_traceback_and_md_keeps_serving(md) -> None:
    with pytest.raises(bridge.BridgeError) as failure:
        bridge.run_script("import pattern_api\npattern_api.NoSuchCall()")

    assert "NoSuchCall" in str(failure.value)
    assert bridge.ping()["ok"]


def test_a_term_search_carries_signatures_but_an_empty_one_does_not() -> None:
    """The compact index must not cost the signatures a real search is for.

    Measured 2026-09-19 on 2026.0.315: 688 calls, ~99KB with every signature, ~32KB as
    names alone. An empty search is what a mistyped argument name silently produces.

    Read-only, so unlike the rest of this file it does not take the ``md`` fixture
    and does not need an empty scene -- it never calls NewProject.
    """
    try:
        named = bridge.api("fabric")
    except bridge.BridgeError as exc:
        pytest.skip(str(exc))
    assert named and any("signatures" in entry for entry in named)

    index = bridge.api("")
    assert len(index) > len(named)
    assert all(set(entry) == {"call"} for entry in index)


def _snapshot_sizes(tmp_path, setter: str) -> list[int]:
    """Six snapshots, one per viewpoint, as PNG byte counts.

    Size rather than hash: MD re-encodes every snapshot, so two captures of an
    unchanged scene are never byte-identical and hashing reports "changed" every time.
    Compressed size does track what is actually in the frame -- a real camera move
    changes it by tens of percent, and a no-op leaves it flat.
    """
    script = f"""
import utility_api as u, export_api as e, os, json
sizes = []
for v in range(6):
    u.{setter}(v)
    u.Refresh3DWindow()
    p = os.path.join(r"{tmp_path}", "{setter}_%d.png" % v)
    e.ExportSnapshot3D(p)
    sizes.append(os.path.getsize(p))
print(json.dumps(sizes))
"""
    return json.loads(bridge.run_script(script)["output"])


def test_setcamviewpoint_moves_the_camera_and_setviewpoint_does_not(tmp_path) -> None:
    """The two calls are one word apart and only one of them does anything.

    Without the working one there is no way to look at the back of a garment, and the
    obvious-looking name fails silently. Measured 2026-09-19 on 2026.0.315: spreads of
    0.0% and 44.2% across the same six indices.

    Needs a garment in the scene -- every view of an empty one is the same empty grey.
    """
    try:
        pieces = bridge.scene().get("pattern_count", 0)
    except bridge.BridgeError as exc:
        pytest.skip(str(exc))
    if not pieces:
        pytest.skip("needs a non-empty scene: every view of an empty one looks alike")

    def spread(sizes: list[int]) -> float:
        return (max(sizes) - min(sizes)) / max(sizes)

    assert spread(_snapshot_sizes(tmp_path, "SetCamViewPoint")) > 0.10
    assert spread(_snapshot_sizes(tmp_path, "SetViewPoint")) < 0.01


def test_two_snapshots_of_one_unchanged_view_are_not_byte_identical(tmp_path) -> None:
    """So that nobody builds change-detection on a hash of the picture.

    Sixteen captures at one camera position gave sixteen different digests, and repeated
    Refresh3DWindow calls do not converge -- it is the encode, not settling.
    """
    try:
        pieces = bridge.scene().get("pattern_count", 0)
    except bridge.BridgeError as exc:
        pytest.skip(str(exc))
    if not pieces:
        pytest.skip("needs a non-empty scene")

    digests = json.loads(
        bridge.run_script(
            f"""
import utility_api as u, export_api as e, os, hashlib, json
u.SetCamViewPoint(0)
out = []
for k in range(3):
    u.Refresh3DWindow()
    p = os.path.join(r"{tmp_path}", "same_%d.png" % k)
    e.ExportSnapshot3D(p)
    out.append(hashlib.sha256(open(p, "rb").read()).hexdigest())
print(json.dumps(out))
"""
        )["output"]
    )
    assert len(set(digests)) == len(digests), "bytes were stable; revisit the docs"


def test_seam_groups_can_be_listed_per_piece_and_stay_in_range() -> None:
    """Per-piece seam membership, which is what makes an unsewn piece findable.

    `GetSeamlinePairGroupCount` rises whether or not the piece a seam was meant for was
    involved, so the total cannot show an orphan. The per-piece list can.
    """
    try:
        pieces = bridge.scene().get("pattern_count", 0)
    except bridge.BridgeError as exc:
        pytest.skip(str(exc))
    if not pieces:
        pytest.skip("needs a sewn garment in the scene")

    data = json.loads(
        bridge.run_script(
            """
import pattern_api as p, json
total = p.GetSeamlinePairGroupCount()
seen = set()
for i in range(p.GetPatternCount()):
    seen.update(p.GetSeamlinePairGroupListInPattern(i))
print(json.dumps({"total": total, "seen": sorted(seen)}))
"""
        )["output"]
    )
    assert all(gid < data["total"] for gid in data["seen"]), (
        "a piece named a seam group outside the count's index space"
    )


def test_an_explicit_deadline_is_honoured_by_a_real_md(md_live) -> None:
    # Offline this is a fake host obeying a sleep. The claim that matters is that a
    # real MD, holding its own main thread, is abandoned on schedule rather than at
    # RUN_TIMEOUT -- which is what makes a hung call cheap to identify.
    started = time.monotonic()
    with pytest.raises(bridge.BridgeError) as failure:
        bridge.run_script("import time; time.sleep(3)", timeout=0.5)
    elapsed = time.monotonic() - started

    assert elapsed < 2.5, f"waited {elapsed:.1f}s, so the deadline was ignored"
    assert "did not answer" in str(failure.value)


def test_md_is_still_serving_after_a_deadline_passed(md_live) -> None:
    # A timeout abandons the reply, not the work, and must not leave the bridge
    # desynchronised: the next request has to get its own answer and not the previous
    # script's late one.
    reply = bridge.run_script("print('still here')")
    assert "still here" in reply.get("output", "")


ROUND_TRIP = """
import pattern_api, export_api, import_api, fabric_api, ApiTypes, json, os

def fingerprint():
    n = pattern_api.GetPatternCount()
    return {{
        "pieces": n,
        "names": [pattern_api.GetPatternPieceName(i) for i in range(n)],
        "fabrics": [pattern_api.GetPatternPieceFabricIndex(i) for i in range(n)],
        "layers": [pattern_api.GetPatternLayer(i) for i in range(n)],
        "seams": pattern_api.GetSeamlinePairGroupCount(),
        "styles": list(fabric_api.GetFabricStyleNameList()),
    }}

before = fingerprint()
path = os.path.join(r"{tmp_path}", "round_trip.zprj")
export_api.ExportZPrj(path)
import_api.ImportZprj(path, ApiTypes.ImportZPRJOption())
print(json.dumps({{"before": before, "after": fingerprint()}}))
"""


def test_a_zprj_survives_being_written_and_read_back(md, tmp_path) -> None:
    # The format is the only thing carrying a garment between sessions, and nothing had
    # ever checked that what comes back is what went out. Pieces, their names, their
    # fabric bindings, their simulation layers, the seam count and the fabric list all
    # have to survive; a silent loss here looks like the garment having been built wrong.
    bridge.run_script(SQUARE)
    reply = bridge.run_script(ROUND_TRIP.format(tmp_path=str(tmp_path).replace("\\", "/")))
    result = json.loads(reply["output"].strip().splitlines()[-1])

    assert result["after"] == result["before"]


def test_every_note_names_a_call_this_build_actually_has(md_live) -> None:
    # A note keyed to a name MD does not expose is a typo, or a call MD renamed between
    # builds. Either way it would silently never appear -- the annotation would rot
    # quietly, which is the one failure mode that makes documentation worse than none.
    # Only a real MD can answer this: the fake host implements a handful of calls.
    from md_mcp import notes

    known = {c["call"] for c in bridge.api("")}
    missing = sorted(set(notes.NOTES) - known)

    assert not missing, f"notes for calls this build does not expose: {missing}"


def test_a_live_search_carries_both_the_signature_and_the_note(md_live) -> None:
    # The whole point: MD supplies the shape, the notes supply the meaning, and a caller
    # searching a call gets both in the one reply.
    entry = next(c for c in bridge.api("Simulate") if c["call"] == "utility_api.Simulate")

    assert entry["signatures"], "MD's own signature must survive annotation"
    assert "solver steps" in entry["note"]
