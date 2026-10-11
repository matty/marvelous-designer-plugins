"""The seam ledger: what MD has sewn to what, decoded from its pattern JSON.

MD's call API counts seam groups and says nothing about which edges they join, so a wrong
seam is invisible until the cloth shows it -- the 2026-10-11 cape session accepted 34
seams a layer clone made and nobody could list them. ``ExportPatternJSON`` does say:
each group's pairs name a piece and a span along its outline. Measured on 2026.0.315
(the cape session's export, ``tests/fixtures/cape_back_and_front.json``):

* a side's ``ShapeID`` is the piece's own ``ID`` in ``PatternList``;
* ``LengthParam.fStart``/``fEnd`` are fractions of the outline's length, measured from
  the first point of the first line, and run backwards on a reversed side (0.596 to
  0.553 on the front of a shoulder seam whose back ran 0.784 to 0.807);
* consecutive outline lines share their end points, so the outline's length is the sum
  of its lines'.

This module is plain Python and never touches MD: the snippet in ``bridge`` exports the
document inside MD and sends back the pieces, MD's own line lengths and the seam groups,
and everything below turns that into a ledger. That keeps the decoding testable without
MD, against a real export.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

#: Spans that meet or overlap by less than this are adjacent, not overlapping. A seam
#: ending exactly where the next begins is ordinary construction.
TOUCH_MM = 1.0

#: MD's own Check Sewing Length highlights a difference over 5 mm (MD help). A deliberate
#: ease can be more; this is a prompt to check, not an error.
LENGTH_DIFFERENCE_MM = 5.0

#: When MD's line lengths and the JSON's own geometry disagree by more than this, the
#: JSON's line order may not be MD's line numbering -- which every line index in the
#: ledger depends on. Curves sampled as polylines run a little short of MD's figure.
LINE_ORDER_TOLERANCE = 0.02

#: How many warnings of one kind are listed before the rest are only counted.
MAX_WARNINGS_PER_KIND = 20


def polyline_lengths(line_list: list[dict]) -> list[float]:
    """Each outline line's length from its points, in millimetres."""
    lengths = []
    for line in line_list:
        points = [
            (point["Position"]["x"], point["Position"]["y"])
            for point in line.get("PointList", [])
        ]
        lengths.append(sum(math.dist(a, b) for a, b in zip(points, points[1:])))
    return lengths


def raw_from_document(
    document: dict, line_lengths: dict[str, list[float]] | None = None
) -> dict:
    """What the MD snippet sends, built from a pattern JSON document instead.

    ``line_lengths`` maps a piece's ``ID`` to MD's ``GetLineLength`` values, when known.
    """
    pieces = []
    for index, piece in enumerate(document.get("PatternList", [])):
        lines = (piece.get("ShapeInfo") or {}).get("LineList") or []
        pieces.append(
            {
                "id": piece.get("ID"),
                "name": piece.get("Name"),
                "index": index,
                "line_lengths": (line_lengths or {}).get(piece.get("ID")),
                "polyline_lengths": polyline_lengths(lines),
            }
        )
    return {"pieces": pieces, "groups": document.get("SeamLinePairGroupList", [])}


def _spans(lengths: list[float]) -> list[tuple[float, float]]:
    spans, start = [], 0.0
    for length in lengths:
        spans.append((start, start + length))
        start += length
    return spans


def _side(raw_side: dict, pieces: dict[str, dict]) -> dict:
    piece = pieces.get(raw_side.get("ShapeID"))
    params = raw_side.get("LengthParam") or {}
    start, end = float(params.get("fStart", 0.0)), float(params.get("fEnd", 0.0))
    side: dict[str, Any] = {"direction": raw_side.get("Direction")}
    if piece is None:
        side.update(piece=None, shape_id=raw_side.get("ShapeID"), fraction=[start, end])
        return side
    perimeter = piece["perimeter"]
    from_mm, to_mm = start * perimeter, end * perimeter
    low, high = min(from_mm, to_mm), max(from_mm, to_mm)
    lines = [
        index
        for index, (line_start, line_end) in enumerate(piece["spans"])
        if min(high, line_end) - max(low, line_start) > TOUCH_MM
    ]
    side.update(
        piece=piece["name"],
        piece_index=piece["index"],
        from_mm=round(from_mm, 1),
        to_mm=round(to_mm, 1),
        lines=lines,
        reversed=start > end,
    )
    return side


def _length(side: dict) -> float | None:
    if side.get("piece") is None:
        return None
    return round(abs(side["to_mm"] - side["from_mm"]), 1)


def _key(side: dict) -> tuple:
    if side.get("piece") is None:
        return ("?", side.get("shape_id"), tuple(side.get("fraction", ())))
    return (side["piece"], round(side["from_mm"]), round(side["to_mm"]), side["direction"])


def _pair_key(pair: dict) -> tuple:
    """The same seam whichever side MD listed first."""
    return tuple(sorted((_key(pair["a"]), _key(pair["b"])), key=repr))


def _describe(pair: dict) -> str:
    def one(side: dict) -> str:
        if side.get("piece") is None:
            return "unknown piece {}".format(side.get("shape_id"))
        return "{} {:.0f}-{:.0f} mm (lines {})".format(
            side["piece"], side["from_mm"], side["to_mm"],
            ", ".join(str(line) for line in side["lines"]) or "none",
        )

    return "{} <-> {}".format(one(pair["a"]), one(pair["b"]))


def ledger(raw: dict, max_groups: int = 200) -> dict:
    """The seam ledger, its warnings and a fingerprint, from what the snippet sent."""
    warnings: dict[str, list[str]] = {}

    def warn(kind: str, message: str) -> None:
        warnings.setdefault(kind, []).append(message)

    pieces: dict[str, dict] = {}
    for piece in raw.get("pieces", []):
        measured = piece.get("line_lengths")
        polyline = piece.get("polyline_lengths") or []
        lengths = measured if measured else polyline
        if measured and polyline and len(measured) == len(polyline):
            off = [
                index for index, (a, b) in enumerate(zip(measured, polyline))
                if a > 0 and abs(a - b) / a > LINE_ORDER_TOLERANCE
            ]
            if off:
                warn(
                    "line_order",
                    "{}: MD's lengths for lines {} differ from the JSON's own geometry by "
                    "more than {:.0%}, so its line numbers may not be MD's".format(
                        piece.get("name"), off, LINE_ORDER_TOLERANCE
                    ),
                )
        pieces[piece.get("id")] = {
            "name": piece.get("name"),
            "index": piece.get("index"),
            "perimeter": sum(lengths),
            "spans": _spans(lengths),
        }

    groups = []
    for index, group in enumerate(raw.get("groups", [])):
        fold = group.get("FoldData") or {}
        pairs = []
        for raw_pair in group.get("PairList", []):
            a = _side(raw_pair.get("First") or {}, pieces)
            b = _side(raw_pair.get("Second") or {}, pieces)
            pair = {"a": a, "b": b, "length_mm": [_length(a), _length(b)]}
            pairs.append(pair)
            for side in (a, b):
                if side.get("piece") is None:
                    warn("unknown_piece", "group {} names a piece that is not in the "
                         "export ({})".format(group.get("Name"), side.get("shape_id")))
            la, lb = pair["length_mm"]
            if la is not None and lb is not None and abs(la - lb) > LENGTH_DIFFERENCE_MM:
                warn("length", "{}: the two sides differ by {:.0f} mm -- fine if eased on "
                     "purpose, otherwise MD spreads the difference along the seam".format(
                         group.get("Name"), abs(la - lb)))
        groups.append(
            {
                "index": index,
                "name": group.get("Name"),
                "turned": group.get("bIsTurned"),
                "fold": {"angle": fold.get("iAngle"), "strength": fold.get("iStrength")},
                "pairs": pairs,
            }
        )

    # Every side, so an edge sewn twice -- or to itself -- shows up wherever it is.
    sides = []
    for group in groups:
        for number, pair in enumerate(group["pairs"]):
            for side in (pair["a"], pair["b"]):
                if side.get("piece") is not None:
                    sides.append((group, number, side))
    for group in groups:
        for pair in group["pairs"]:
            a, b = pair["a"], pair["b"]
            if a.get("piece") is not None and a.get("piece") == b.get("piece"):
                if _overlap(a, b) > TOUCH_MM:
                    warn("self", "{}: {} is sewn to an overlapping part of itself".format(
                        group["name"], a["piece"]))
    for i, (group_i, pair_i, side_i) in enumerate(sides):
        for group_j, pair_j, side_j in sides[i + 1:]:
            if (group_i is group_j and pair_i == pair_j) or side_i["piece"] != side_j["piece"]:
                continue
            overlap = _overlap(side_i, side_j)
            if overlap > TOUCH_MM:
                warn("reused", "{} {:.0f}-{:.0f} mm is sewn by both {} and {} ({:.0f} mm "
                     "shared)".format(side_i["piece"], *sorted((side_i["from_mm"],
                     side_i["to_mm"])), group_i["name"], group_j["name"], overlap))

    count = raw.get("seam_group_count")
    if count is not None and count != len(groups):
        warn("count", "MD counts {} seam group(s) and its export lists {}".format(
            count, len(groups)))

    canonical = sorted(
        (_pair_key(pair), group["turned"]) for group in groups for pair in group["pairs"]
    )
    fingerprint = hashlib.sha256(
        json.dumps(canonical, default=str).encode("utf-8")
    ).hexdigest()[:16]

    result: dict[str, Any] = {
        "fingerprint": fingerprint,
        "group_count": len(groups),
        "groups": groups[:max_groups],
    }
    if len(groups) > max_groups:
        result["groups_omitted"] = len(groups) - max_groups
    listed: dict[str, Any] = {}
    for kind, messages in warnings.items():
        listed[kind] = messages[:MAX_WARNINGS_PER_KIND]
        if len(messages) > MAX_WARNINGS_PER_KIND:
            listed[kind + "_omitted"] = len(messages) - MAX_WARNINGS_PER_KIND
    result["warnings"] = listed
    return result


def _overlap(a: dict, b: dict) -> float:
    low_a, high_a = sorted((a["from_mm"], a["to_mm"]))
    low_b, high_b = sorted((b["from_mm"], b["to_mm"]))
    return min(high_a, high_b) - max(low_a, low_b)


def diff(before: dict, after: dict) -> dict:
    """Seams in ``after`` and not ``before``, and the other way round."""

    def pairs(ledger_: dict) -> dict[tuple, dict]:
        return {
            _pair_key(pair): {"group": group["name"], "seam": _describe(pair)}
            for group in ledger_.get("groups", [])
            for pair in group["pairs"]
        }

    old, new = pairs(before), pairs(after)
    return {
        "added": [new[key] for key in new if key not in old],
        "removed": [old[key] for key in old if key not in new],
    }
