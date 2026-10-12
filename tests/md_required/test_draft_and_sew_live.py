"""md_draft_piece and md_sew against a live Marvelous Designer.

What the fake host cannot settle: that MD numbers a drafted piece's lines as planned,
that its spline measures what ``drafting.edge_length`` predicts, that the edge names
stick in the garment's metadata and travel in a checkpoint, and that a seam sewn by
name is the seam MD's ledger then holds. First run green on 2026-10-12, 2026.0.315.

Needs an empty scene: it drafts, sews, checkpoints and calls NewProject.
"""

from __future__ import annotations

import json

import pytest
from mcp import Client

from md_mcp import bridge
from md_mcp.server import build_server

pytestmark = pytest.mark.md_required


def _outline(curve: bool) -> list[dict]:
    outline = [{"at": [0, 0], "edge": "hem"}, {"at": [400, 0], "edge": "side"},
               {"at": [400, 500], "edge": "top"}, {"at": [0, 500], "edge": "centre"}]
    if curve:
        outline[2]["through"] = [[300, 540], [200, 555], [100, 540]]
    return outline


def _call(name: str, args: dict):
    import anyio

    async def run():
        async with Client(build_server()) as client:
            return await client.call_tool(name, args)

    result = anyio.run(run)
    if result.is_error:
        return result.content[0].text
    return json.loads(result.content[0].text)


@pytest.fixture
def empty(md):
    bridge.call("utility_api.NewProject")
    yield
    bridge.call("utility_api.NewProject")


def test_a_drafted_piece_is_numbered_and_measured_as_planned(empty) -> None:
    drafted = _call("md_draft_piece", {"name": "back", "outline": _outline(curve=True)})
    assert isinstance(drafted, dict), drafted
    assert "errors" not in drafted, drafted
    assert drafted["edges_stored"] is True
    for edge in drafted["edges"]:
        # Measured: 418.53 predicted, 418.52 in MD, for the curved top.
        assert edge["md_length_mm"] == pytest.approx(edge["predicted_length_mm"], abs=0.1)
    assert drafted["edges"][2]["curved"] and drafted["edges"][2]["md_length_mm"] > 400


def test_a_seam_sewn_by_name_is_the_seam_md_holds(empty) -> None:
    _call("md_draft_piece", {"name": "front", "outline": _outline(curve=False)})
    _call("md_draft_piece", {"name": "back", "outline": _outline(curve=True)})
    sewn = _call("md_sew", {"a": "front.side", "b": "back.centre", "ends": "opposite"})
    assert isinstance(sewn, dict), f"md_sew refused: {sewn}"
    assert sewn["ok"] is True, sewn
    pair = _call("md_seams", {})["groups"][0]["pairs"][0]
    assert (pair["a"]["piece"], pair["a"]["lines"]) == ("front", [1])
    assert (pair["b"]["piece"], pair["b"]["lines"], pair["b"]["reversed"]) == (
        "back", [3], True)
    refused = _call("md_sew", {"a": "front.side", "b": "back.side", "ends": "opposite"})
    assert isinstance(refused, str) and "already sewn" in refused


def test_edge_names_travel_in_a_checkpoint(empty) -> None:
    _call("md_draft_piece", {"name": "front", "outline": _outline(curve=False)})
    _call("md_checkpoint", {"label": "edge-names-live-test"})
    bridge.call("utility_api.NewProject")
    assert _call("md_edges", {})["pieces"] == {}
    _call("md_restore", {"checkpoint": "edge-names-live-test"})
    restored = _call("md_edges", {})["pieces"]
    assert "front" in restored, f"no edge names came back with the garment: {restored}"
    assert restored["front"]["valid"] is True
