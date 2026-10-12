"""md_draft_piece, md_sew and md_edges end to end, against the fake MD host.

The shipped listener runs the tools' snippets against fake host modules that number
lines corner to corner, as MD was measured to. That proves the round trips, the guards
and the bookkeeping -- the edge map written into the garment's metadata and read back,
the seam ledger before and after. It proves nothing about Marvelous Designer; the live
tests in ``tests/md_required`` do that.
"""

from __future__ import annotations

import json

import pytest

from md_mcp import drafting
from md_mcp.server import build_server
from tests.fake_md_host import running_listener
from tests.test_server import call, error_text, payload


@pytest.fixture
def md(monkeypatch):
    with running_listener(monkeypatch) as host:
        yield host


@pytest.fixture
def server():
    return build_server()


def square(at=(0, 0), size=300, names=("bottom", "right", "top", "left")):
    x, y = at
    corners = [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]
    return [{"at": list(c), "edge": n} for c, n in zip(corners, names)]


def test_drafting_builds_names_lays_out_and_stores(md, server):
    first = payload(call(server, "md_draft_piece", {"name": "front", "outline": square()}))
    assert first["index"] == 0 and first["edges_stored"] is True
    assert [e["md_length_mm"] for e in first["edges"]] == [300.0] * 4
    assert md.piece_names == ["front"]

    second = payload(call(server, "md_draft_piece", {"name": "back", "outline": square()}))
    # Drafted at the origin too, and laid out 50 mm right of the first.
    assert second["bbox"]["min"] == [350.0, 0.0]
    assert md.piece_points[1][0][:2] == (350.0, 0.0)

    stored = json.loads(md.metadata[drafting.METADATA_KEY])
    assert sorted(stored["pieces"]) == ["back", "front"]
    assert stored["pieces"]["back"]["edges"] == {"bottom": 0, "right": 1, "top": 2, "left": 3}


def test_curve_points_reach_md_as_type_two_inside_one_line(md, server):
    outline = square()
    outline[2]["through"] = [[200, 360], [100, 360]]
    result = payload(call(server, "md_draft_piece", {"name": "panel", "outline": outline}))
    assert [p[2] for p in md.piece_points[0]] == [0, 0, 0, 2, 2, 0]
    assert len(result["edges"]) == 4
    # The fake measures a curve as a polyline, MD as a spline: a warning, not an error.
    assert result["edges_stored"] is True
    assert any("'top'" in w for w in result["warnings"])


def test_a_name_already_in_the_scene_is_refused_before_md_is_touched(md, server):
    payload(call(server, "md_draft_piece", {"name": "front", "outline": square()}))
    result = call(server, "md_draft_piece", {"name": "front", "outline": square()})
    assert "already exists" in error_text(result)
    assert len(md.piece_names) == 1


def test_a_refused_store_is_reported_not_assumed(md, server):
    md.metadata_keeps = False
    result = payload(call(server, "md_draft_piece", {"name": "front", "outline": square()}))
    assert result["edges_stored"] is False
    assert any("did not keep the edge names" in w for w in result["warnings"])


def test_sewing_by_name_makes_and_confirms_one_seam(md, server):
    payload(call(server, "md_draft_piece", {"name": "front", "outline": square()}))
    payload(call(server, "md_draft_piece", {"name": "back", "outline": square()}))
    result = payload(call(server, "md_sew", {"a": "front.right", "b": "back.left",
                                             "ends": "opposite"}))
    assert result["ok"] is True, result
    assert md.seams == [(0, 1, 1, 3, True, False)]
    assert len(result["added"]) == 1


def test_sewing_refuses_before_touching_md(md, server):
    payload(call(server, "md_draft_piece", {"name": "front", "outline": square()}))
    payload(call(server, "md_draft_piece", {"name": "back", "outline": square(size=200)}))
    for args, message in [
        ({"a": "front.right", "b": "front.right", "ends": "same"}, "same edge"),
        ({"a": "front.right", "b": "back.left", "ends": "opposite"}, "over the 5 mm"),
        ({"a": "front.right", "b": "back.left"}, "ends"),
    ]:
        result = call(server, "md_sew", args)
        assert result.is_error and message in error_text(result), error_text(result)
    assert md.seams == []


def test_an_edge_sewn_once_is_not_sewn_again_by_accident(md, server):
    payload(call(server, "md_draft_piece", {"name": "front", "outline": square()}))
    payload(call(server, "md_draft_piece", {"name": "back", "outline": square()}))
    payload(call(server, "md_sew", {"a": "front.right", "b": "back.left", "ends": "opposite"}))
    again = call(server, "md_sew", {"a": "front.right", "b": "back.right", "ends": "opposite"})
    assert "already sewn" in error_text(again)
    layered = payload(call(server, "md_sew", {"a": "front.right", "b": "back.right",
                                              "ends": "opposite", "allow_layered": True}))
    assert len(md.seams) == 2
    # Layered on purpose, and the ledger says so: the result is not quietly "ok".
    assert layered["ok"] is False and "reused" in layered["problems"][0]


def test_edges_lists_stored_names_and_notices_a_reshape(md, server):
    payload(call(server, "md_draft_piece", {"name": "front", "outline": square()}))
    listed = payload(call(server, "md_edges", {}))["pieces"]
    assert listed["front"]["valid"] is True
    md.piece_points[0][2] = (340.0, 300.0, 0)
    listed = payload(call(server, "md_edges", {"piece": "front"}))["pieces"]
    assert listed["front"]["valid"] is False and "moved relative" in listed["front"]["why"]
    sew = call(server, "md_sew", {"a": "front.right", "b": "front.left", "ends": "same"})
    assert "no longer fit" in error_text(sew)


def test_fit_finds_seams_in_3d_through_the_bridge(md, server):
    # Flat on the board: the fake puts unarranged pieces at their 2D coordinates.
    payload(call(server, "md_draft_piece", {"name": "front", "outline": square()}))
    payload(call(server, "md_draft_piece", {"name": "back", "outline": square()}))
    payload(call(server, "md_sew", {"a": "front.right", "b": "back.left", "ends": "opposite"}))
    report = payload(call(server, "md_fit", {"body": False,
                                             "rings": {"bottoms": ["front.bottom", "back.bottom"]}}))
    assert report["problems"] == []
    (seam,) = report["seams"]
    # front's right edge is x = 300, back's left x = 350, the same heights: 50 mm apart.
    assert seam["gap_mm"]["min"] == pytest.approx(50, abs=0.5)
    assert seam["gap_mm"]["max"] == pytest.approx(50, abs=0.5)
    # Reported by the names the pieces were drafted with, not their line numbers.
    assert seam["seam"] == "front.right <-> back.left"
    for piece in report["pieces"].values():
        assert piece["registration_worst_mm"] < 0.5
    ring = report["rings"]["bottoms"]
    assert ring["edges"] == ["front.bottom", "back.bottom"]
    assert ring["length_mm"] == pytest.approx(600) and ring["stretch_pct"] == pytest.approx(0)
    draped = payload(call(server, "md_fit", {"body": False, "draped": True}))
    assert any("front.right <-> back.left" in p and "did not close" in p
               for p in draped["problems"])
