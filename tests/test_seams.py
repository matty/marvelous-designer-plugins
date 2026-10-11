"""md_seams: the seam ledger, decoded from MD's pattern JSON.

The decoding is tested against a real export -- the cape session's back and left front,
``tests/fixtures/cape_back_and_front.json`` -- so the field names and the fraction
convention are MD's, not a guess. The tool end to end runs against the fake host, whose
export is built in the same shape; that proves the plumbing and nothing about MD.
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

from md_mcp import bridge, seams
from md_mcp.server import build_server
from tests.fake_md_host import running_listener
from tests.test_server import call, payload

CAPE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "cape_back_and_front.json").read_text(
        encoding="utf-8"
    )
)


def cape_ledger(document: dict = CAPE, **raw_extra) -> dict:
    raw = seams.raw_from_document(document)
    raw.update(raw_extra)
    return seams.ledger(raw)


# --------------------------------------------------------------- against a real export


def test_a_real_shoulder_seam_decodes_to_the_lines_it_joins() -> None:
    # Measured in the cape session: the back's left shoulder ran 0.784-0.807 of its
    # outline and the front's 0.596-0.553, backwards. Both are the 160 mm shoulder edge.
    ledger = cape_ledger()

    (group,) = ledger["groups"]
    (pair,) = group["pairs"]
    assert pair["a"]["piece"] == "Cape_Back" and pair["a"]["lines"] == [7]
    assert pair["b"]["piece"] == "Cape_Front_L" and pair["b"]["lines"] == [2]
    assert pair["b"]["reversed"] and not pair["a"]["reversed"]
    assert pair["length_mm"] == [pytest.approx(160.1, abs=0.2)] * 2
    assert ledger["warnings"] == {}


def test_mds_own_line_lengths_are_used_when_they_are_given() -> None:
    raw = seams.raw_from_document(CAPE)
    raw["pieces"][0]["line_lengths"] = [
        round(length, 1) for length in raw["pieces"][0]["polyline_lengths"]
    ]

    assert seams.ledger(raw)["warnings"] == {}


def test_line_lengths_that_disagree_with_the_geometry_are_flagged() -> None:
    # If MD numbered lines differently from the document, every line index would be
    # wrong -- and quietly so. A length mismatch is how that would show.
    raw = seams.raw_from_document(CAPE)
    raw["pieces"][0]["line_lengths"] = list(reversed(raw["pieces"][0]["polyline_lengths"]))

    assert "line_order" in seams.ledger(raw)["warnings"]


# -------------------------------------------------------------------------- warnings


def with_extra_pair(first: dict, second: dict, name: str = "Extra") -> dict:
    document = copy.deepcopy(CAPE)
    document["SeamLinePairGroupList"].append(
        {"Name": name, "bIsTurned": True, "FoldData": {"iAngle": 180, "iStrength": 5},
         "PairList": [{"First": first, "Second": second}]}
    )
    return document


def span(shape: str, start: float, end: float, direction: bool = True) -> dict:
    return {"ShapeID": shape, "LengthParam": {"fStart": start, "fEnd": end},
            "Direction": direction}


def test_an_edge_in_two_seams_is_reported() -> None:
    # What a layer clone does: the shell's shoulder, already sewn to the front, sewn
    # again to the clone. The second seam is the one nobody planned.
    document = with_extra_pair(span("fk3LDE", 0.78378677, 0.80686474),
                               span("CM7kcV", 0.10, 0.12), name="Clone_shoulder")

    warnings = cape_ledger(document)["warnings"]

    assert len(warnings["reused"]) == 1
    assert "Cape_Back" in warnings["reused"][0] and "Clone_shoulder" in warnings["reused"][0]


def test_an_edge_sewn_to_itself_is_reported() -> None:
    document = with_extra_pair(span("fk3LDE", 0.10, 0.20), span("fk3LDE", 0.15, 0.25))

    assert "self" in cape_ledger(document)["warnings"]


def test_adjacent_seams_are_not_overlapping_ones() -> None:
    # A seam ending where the next begins is ordinary construction.
    document = with_extra_pair(span("fk3LDE", 0.80686474, 0.85), span("CM7kcV", 0.0, 0.05))

    assert "reused" not in cape_ledger(document)["warnings"]


def test_sides_of_different_lengths_are_flagged_and_a_missing_piece_too() -> None:
    document = with_extra_pair(span("fk3LDE", 0.0, 0.05), span("nowhere", 0.0, 0.5))
    document["SeamLinePairGroupList"].append(
        {"Name": "Uneven", "bIsTurned": False, "FoldData": {},
         "PairList": [{"First": span("fk3LDE", 0.30, 0.40),
                       "Second": span("CM7kcV", 0.0, 0.05)}]}
    )

    warnings = cape_ledger(document)["warnings"]

    assert "unknown_piece" in warnings
    assert any("Uneven" in message for message in warnings["length"])


def test_a_count_that_disagrees_with_the_export_is_flagged() -> None:
    assert "count" in cape_ledger(seam_group_count=3)["warnings"]


# ------------------------------------------------------------------------- the diff


def test_the_diff_names_what_was_added_and_what_was_removed() -> None:
    before = cape_ledger()
    after = cape_ledger(with_extra_pair(span("fk3LDE", 0.0, 0.05),
                                        span("CM7kcV", 0.0, 0.05), name="New"))

    change = seams.diff(before, after)

    assert [entry["group"] for entry in change["added"]] == ["New"]
    assert change["removed"] == []
    assert seams.diff(after, before)["removed"][0]["group"] == "New"
    assert before["fingerprint"] != after["fingerprint"]


def test_the_same_seam_listed_the_other_way_round_is_not_a_change() -> None:
    swapped = copy.deepcopy(CAPE)
    pair = swapped["SeamLinePairGroupList"][0]["PairList"][0]
    pair["First"], pair["Second"] = pair["Second"], pair["First"]

    assert seams.diff(cape_ledger(), cape_ledger(swapped)) == {"added": [], "removed": []}
    assert cape_ledger()["fingerprint"] == cape_ledger(swapped)["fingerprint"]


# ------------------------------------------------------------- the tool, end to end


PANELS = """
for name, x in (("front", 0.0), ("back", 400.0)):
    index = pattern_api.CreatePatternWithPoints(
        [(x, 0, 0), (x + 360, 0, 0), (x + 360, 500, 0), (x, 500, 0)])
    pattern_api.SetPatternPieceName(index, name)
"""


@pytest.fixture
def md(monkeypatch):
    with running_listener(monkeypatch) as host:
        yield host


def test_md_seams_reads_back_the_seams_that_were_sewn(md) -> None:
    server = build_server()
    bridge.run_script(PANELS)
    bridge.call("pattern_api.AddSeamlinePairGroup", [0, 1, 1, 3, True, False])

    ledger = payload(call(server, "md_seams"))

    (group,) = ledger["groups"]
    (pair,) = group["pairs"]
    assert (pair["a"]["piece"], pair["a"]["lines"]) == ("front", [1])
    assert (pair["b"]["piece"], pair["b"]["lines"]) == ("back", [3])
    assert pair["length_mm"] == [500.0, 500.0]
    assert ledger["warnings"] == {}
    assert md.written and not any(pathlib.Path(p).exists() for p in md.written)


def test_md_seams_since_shows_a_seam_nobody_planned(md) -> None:
    server = build_server()
    bridge.run_script(PANELS)
    bridge.call("pattern_api.AddSeamlinePairGroup", [0, 1, 1, 3, True, False])
    first = payload(call(server, "md_seams"))

    # The same front edge sewn a second time, as a layer clone would.
    bridge.call("pattern_api.AddSeamlinePairGroup", [0, 1, 1, 1, True, False])
    second = payload(call(server, "md_seams", {"since": first["fingerprint"]}))

    assert len(second["added"]) == 1 and second["removed"] == []
    assert "front" in second["warnings"]["reused"][0]


def test_an_unknown_fingerprint_says_why_there_is_no_diff(md) -> None:
    ledger = payload(call(build_server(), "md_seams", {"since": "0123456789abcdef"}))

    assert "only the last 20" in ledger["since"]
    assert "added" not in ledger


def test_groups_are_capped_and_the_rest_counted(md) -> None:
    server = build_server()
    bridge.run_script(PANELS)
    for line in range(3):
        bridge.call("pattern_api.AddSeamlinePairGroup", [0, line, 1, line, True, False])

    ledger = payload(call(server, "md_seams", {"max_groups": 1}))

    assert len(ledger["groups"]) == 1 and ledger["groups_omitted"] == 2
    assert ledger["group_count"] == 3
