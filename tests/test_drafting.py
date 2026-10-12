"""Planning a piece from named edges, and a seam from two of them -- no MD involved.

What is tested here is md-mcp's own reasoning: the point list and line map it derives,
what it refuses, how it lays a piece out on the 2D board, and how it judges MD's read
back. Whether MD builds what the plan says is ``tests/md_required``'s question.
"""

from __future__ import annotations

import json
import math

import pytest

from md_mcp import drafting, seams, sewing

SQUARE = [
    {"at": [0, 0], "edge": "bottom"},
    {"at": [300, 0], "edge": "right"},
    {"at": [300, 300], "edge": "top"},
    {"at": [0, 300], "edge": "left"},
]


def test_each_corner_starts_the_line_of_the_same_number():
    drafted = drafting.plan("square", SQUARE)
    assert [edge["line"] for edge in drafted["edges"]] == [0, 1, 2, 3]
    assert drafted["points"] == [(0.0, 0.0, 0), (300.0, 0.0, 0), (300.0, 300.0, 0),
                                 (0.0, 300.0, 0)]
    assert [edge["predicted_length_mm"] for edge in drafted["edges"]] == [300.0] * 4
    assert drafted["winding"] == "counter-clockwise"
    assert drafted["area_mm2"] == 90000.0


def test_curve_points_belong_to_their_edge_and_do_not_add_lines():
    outline = [
        {"at": [0, 0], "edge": "hem"},
        {"at": [400, 0], "edge": "side"},
        {"at": [400, 500], "edge": "top", "through": [[300, 560], [200, 580], [100, 560]]},
        {"at": [0, 500], "edge": "back"},
    ]
    drafted = drafting.plan("panel", outline)
    kinds = [kind for _, _, kind in drafted["points"]]
    assert kinds == [0, 0, 0, 2, 2, 2, 0]
    assert len(drafted["edges"]) == 4
    top = drafted["edges"][2]
    assert top["curved"] and top["line"] == 2
    # Longer than the chords through its points: the curve bulges between them.
    chords = sum(math.dist(a, b) for a, b in zip(
        [(400, 500), (300, 560), (200, 580), (100, 560), (0, 500)],
        [(300, 560), (200, 580), (100, 560), (0, 500)]))
    assert chords < top["predicted_length_mm"] < chords * 1.02


def test_the_spline_reproduces_a_circle_closely():
    # Quarter circle of radius 1000 sampled at five points: a natural cubic through
    # them should come within a fraction of a percent of the true arc.
    points = [(1000 * math.cos(t), 1000 * math.sin(t))
              for t in [k * math.pi / 8 for k in range(5)]]
    assert drafting.edge_length(points) == pytest.approx(1000 * math.pi / 2, rel=2e-3)


@pytest.mark.parametrize(
    ("outline", "message"),
    [
        (SQUARE + [{"at": [0, 0], "edge": "again"}], "repeats the first"),
        ([{"at": [0, 0], "edge": "a"}, {"at": [0, 0], "edge": "b"},
          {"at": [10, 10], "edge": "c"}], "same place"),
        ([{"at": [0, 0], "edge": "a"}, {"at": [300, 300], "edge": "b"},
          {"at": [300, 0], "edge": "c"}, {"at": [0, 300], "edge": "d"}], "crosses itself"),
        ([{"at": [0, 0], "edge": "a"}, {"at": [1, 0], "edge": "a"},
          {"at": [0, 1], "edge": "b"}], "used twice"),
        ([{"at": [0, 0], "edge": "1st"}, {"at": [1, 0], "edge": "b"},
          {"at": [0, 1], "edge": "c"}], "letters, digits"),
        ([{"at": [0, 0]}, {"at": [1, 0], "edge": "b"}, {"at": [0, 1], "edge": "c"}],
         "needs an 'edge'"),
        ([{"at": [0, 0], "edge": "a"}, {"at": [10, 0], "edge": "b"}], "is a line"),
        ([{"at": [0, 0], "edge": "a"}, {"at": [10, 0], "edge": "b"},
          {"at": [20, 0], "edge": "c"}], "no area"),
        ([{"at": [0, "x"], "edge": "a"}, {"at": [10, 0], "edge": "b"},
          {"at": [0, 10], "edge": "c"}], "[x, y]"),
        ([{"at": [0, 0], "edge": "a", "type": 3}, {"at": [10, 0], "edge": "b"},
          {"at": [0, 10], "edge": "c"}], "unknown keys"),
    ],
)
def test_refuses_what_md_would_build_wrong(outline, message):
    with pytest.raises(drafting.DraftError, match=message):
        drafting.plan("piece", outline)


def test_a_curve_that_crosses_another_edge_is_refused():
    outline = [
        {"at": [0, 0], "edge": "hem"},
        {"at": [400, 0], "edge": "side"},
        # Swings below the hem on its way back.
        {"at": [400, 300], "edge": "top", "through": [[200, -200]]},
    ]
    with pytest.raises(drafting.DraftError, match="crosses itself"):
        drafting.plan("piece", outline)


def test_layout_puts_a_piece_right_of_everything_with_bottoms_aligned():
    drafted = drafting.plan("square", SQUARE)
    existing = [{"min": [-100, -50], "max": [200, 400], "name": "a"},
                {"min": [500, 20], "max": [900, 300], "name": "b"}]
    moved, warnings = drafting.lay_out(drafted, existing, gap=50)
    assert moved["bbox"]["min"] == [950.0, -50.0]
    assert moved["points"][0] == (950.0, -50.0, 0)
    assert moved["edges"][1]["start"] == [1250.0, -50.0]
    assert warnings == []


def test_layout_keeps_an_explicit_place_and_names_what_it_overlaps():
    drafted = drafting.plan("square", SQUARE)
    existing = [{"min": [0, 0], "max": [100, 100], "name": "under"}]
    moved, warnings = drafting.lay_out(drafted, existing, at=[50, 50])
    assert moved["bbox"]["min"] == [50.0, 50.0]
    assert warnings == ["it overlaps piece 'under' on the 2D board"]


def test_board_box_is_built_from_md_centre_and_string_size():
    box = drafting.board_box([150.0, 150.0], {"Height": "300.000000", "Width": "300.000000"})
    assert box == {"min": [0.0, 0.0], "max": [300.0, 300.0]}


def _read_back(points, lengths):
    return {"points": points, "lengths": lengths}


def test_verify_accepts_what_was_planned():
    drafted = drafting.plan("square", SQUARE)
    errors, warnings, _ = drafting.verify(
        drafted, _read_back([[0, 0], [300, 0], [300, 300], [0, 300]], [300.0] * 4))
    assert errors == [] and warnings == []


def test_verify_refuses_an_edge_map_md_did_not_build():
    drafted = drafting.plan("square", SQUARE)
    errors, _, _ = drafting.verify(drafted, _read_back([[0, 0], [300, 0], [0, 300]],
                                                        [300.0] * 3))
    assert "not the planned edges" in errors[0]
    # Rotated numbering: same corners, MD starting from a different one.
    errors, _, _ = drafting.verify(
        drafted, _read_back([[300, 0], [300, 300], [0, 300], [0, 0]], [300.0] * 4))
    assert any("should start at" in error for error in errors)


def test_a_curve_measuring_differently_is_a_warning_not_an_error():
    outline = [{"at": [0, 0], "edge": "hem"}, {"at": [400, 0], "edge": "side"},
               {"at": [400, 500], "edge": "top", "through": [[200, 600]]},
               {"at": [0, 500], "edge": "back"}]
    drafted = drafting.plan("panel", outline)
    lengths = [edge["predicted_length_mm"] for edge in drafted["edges"]]
    lengths[2] += 10
    errors, warnings, _ = drafting.verify(drafted, _read_back(
        [edge["start"] for edge in drafted["edges"]], lengths))
    assert errors == []
    assert "'top'" in warnings[0]


def test_parse_input_information_reads_mds_measured_shape():
    text = json.dumps({"Pattern InputInformation": [{
        "LineList": [{"Line count": "2"},
                     {"Line index": "1", "Line length": 20, "Line type": "Straight type"},
                     {"Line index": "0", "Line length": 10, "Line type": "Straight type"}],
        "PointList": [{"Point count": "2"},
                      {"Point index": "0", "Point positionX": 0, "Point positionY": 0},
                      {"Point index": "1", "Point positionX": 10, "Point positionY": 0}],
    }]})
    assert drafting.parse_input_information(text) == {
        "points": [[0.0, 0.0], [10.0, 0.0]], "lengths": [10.0, 20.0]}


def test_a_stored_map_survives_a_move_on_the_board_but_not_a_reshape():
    drafted = drafting.plan("square", SQUARE)
    entry = drafting.store_entry(drafted, [300.0] * 4)
    moved = [[x + 500, y - 20] for x, y in entry["corners"]]
    assert drafting.still_matches(entry, moved) is None
    reshaped = [list(c) for c in moved]
    reshaped[2][0] += 40
    assert "moved relative" in drafting.still_matches(entry, reshaped)
    assert "corners" in drafting.still_matches(entry, moved[:3])


def test_an_unreadable_store_is_an_empty_one():
    for text in ("", "not json", json.dumps({"version": 99}), json.dumps([1])):
        assert drafting.load_store(text)["pieces"] == {}


# --- sewing --------------------------------------------------------------------------


def _pieces():
    return {
        "front": {"index": 0, "count": 1, "points": [[0, 0], [300, 0], [300, 300], [0, 300]],
                  "lengths": [300.0, 300.0, 300.0, 300.0]},
        "back": {"index": 1, "count": 1,
                 "points": [[400, 0], [700, 0], [700, 310], [400, 310]],
                 "lengths": [300.0, 310.0, 300.0, 310.0]},
    }


def _store():
    front = drafting.store_entry(drafting.plan("front", SQUARE), [300.0] * 4)
    back = drafting.store_entry(drafting.plan("back", [
        {"at": [400, 0], "edge": "bottom"}, {"at": [700, 0], "edge": "right"},
        {"at": [700, 310], "edge": "top"}, {"at": [400, 310], "edge": "left"}]),
        [300.0, 310.0, 300.0, 310.0])
    return {"version": 1, "pieces": {"front": front, "back": back}}


EMPTY_LEDGER = {"groups": [], "warnings": {}}


def test_a_seam_resolves_names_to_lines_and_spans():
    side = sewing.resolve("back.right", _pieces(), _store())
    assert side == {"ref": "back.right", "piece": "back", "piece_index": 1, "edge": "right",
                    "line": 1, "length_mm": 310.0, "span_mm": [300.0, 610.0]}
    assert sewing.resolve("front.3", _pieces(), _store())["line"] == 3


def test_ends_set_the_flags_and_there_is_no_default():
    a = sewing.resolve("front.right", _pieces(), _store())
    b = sewing.resolve("back.left", _pieces(), _store())
    planned = sewing.plan(a, b, "opposite", EMPTY_LEDGER, max_difference_mm=20)
    assert planned["args"] == [0, 1, 1, 3, True, False]
    assert sewing.plan(a, b, "same", EMPTY_LEDGER, max_difference_mm=20)["flags"] == [
        True, True]
    with pytest.raises(sewing.SewError, match="no default"):
        sewing.plan(a, b, "", EMPTY_LEDGER)


@pytest.mark.parametrize(
    ("a", "b", "message"),
    [
        ("front.right", "front.right", "same edge"),
        ("front.right", "back.right", "over the 5 mm"),
        ("nobody.right", "back.left", "no piece is named"),
        ("front.sleeve", "back.left", "no edge 'sleeve'"),
        ("front.9", "back.left", "lines 0 to 3"),
        ("front", "back.left", "'piece.edge'"),
    ],
)
def test_seams_md_would_accept_and_build_wrong_are_refused(a, b, message):
    with pytest.raises(sewing.SewError, match=message):
        sewing.plan(sewing.resolve(a, _pieces(), _store()),
                    sewing.resolve(b, _pieces(), _store()), "opposite", EMPTY_LEDGER)


def test_names_from_a_piece_reshaped_since_drafting_are_refused():
    pieces = _pieces()
    pieces["front"]["points"][2] = [340, 300]
    with pytest.raises(sewing.SewError, match="no longer fit"):
        sewing.resolve("front.right", pieces, _store())


def test_a_name_shared_by_two_pieces_is_refused():
    pieces = _pieces()
    pieces["front"]["count"] = 2
    with pytest.raises(sewing.SewError, match="rename one"):
        sewing.resolve("front.right", pieces, _store())


def _ledger_with(*sides):
    """A ledger holding one group per (piece, from_mm, to_mm) pair of sides."""
    groups = []
    for n, (a, b) in enumerate(sides):
        groups.append({"name": f"S{n}", "pairs": [{
            "a": {"piece": a[0], "from_mm": a[1], "to_mm": a[2], "lines": [],
                  "direction": True},
            "b": {"piece": b[0], "from_mm": b[1], "to_mm": b[2], "lines": [],
                  "direction": False},
            "length_mm": [abs(a[2] - a[1]), abs(b[2] - b[1])]}]})
    return {"groups": groups, "warnings": {}}


def test_an_edge_already_sewn_is_refused_unless_layering_is_meant():
    ledger = _ledger_with((("front", 300, 600), ("back", 1220, 910)))
    a = sewing.resolve("front.right", _pieces(), _store())
    b = sewing.resolve("back.right", _pieces(), _store())
    with pytest.raises(sewing.SewError, match="already sewn"):
        sewing.plan(a, b, "opposite", ledger, max_difference_mm=20)
    assert sewing.plan(a, b, "opposite", ledger, max_difference_mm=20,
                       allow_layered=True)["flags"] == [True, False]


def test_an_adjacent_seam_is_not_a_reused_edge():
    ledger = _ledger_with((("front", 0, 300), ("back", 0, 300)))
    a = sewing.resolve("front.right", _pieces(), _store())
    b = sewing.resolve("back.left", _pieces(), _store())
    sewing.plan(a, b, "opposite", ledger, max_difference_mm=20)


def test_confirm_wants_exactly_the_planned_seam():
    a = sewing.resolve("front.right", _pieces(), _store())
    b = sewing.resolve("back.left", _pieces(), _store())
    planned = sewing.plan(a, b, "opposite", EMPTY_LEDGER, max_difference_mm=20)
    right = _ledger_with((("front", 300, 600), ("back", 1220, 910)))
    assert sewing.confirm(planned, EMPTY_LEDGER, right)["ok"]
    wrong = _ledger_with((("front", 0, 300), ("back", 1220, 910)))
    result = sewing.confirm(planned, EMPTY_LEDGER, wrong)
    assert not result["ok"] and "not front.right" in result["problems"][0]
    two = _ledger_with((("front", 300, 600), ("back", 1220, 910)),
                       (("front", 0, 300), ("back", 0, 300)))
    assert "2 new seam pairs" in sewing.confirm(planned, EMPTY_LEDGER, two)["problems"][0]


def test_confirm_reports_new_ledger_warnings():
    a = sewing.resolve("front.right", _pieces(), _store())
    b = sewing.resolve("back.left", _pieces(), _store())
    planned = sewing.plan(a, b, "opposite", EMPTY_LEDGER, max_difference_mm=20)
    after = _ledger_with((("front", 300, 600), ("back", 1220, 910)))
    after["warnings"] = {"reused": ["front 300-600 mm is sewn by both"]}
    assert "new warnings" in sewing.confirm(planned, EMPTY_LEDGER, after)["problems"][0]
    assert seams.TOUCH_MM == 1.0
