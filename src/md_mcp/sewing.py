"""One seam, planned from edge names and checked against what MD then holds.

``AddSeamlinePairGroup`` takes two (piece index, line index) pairs and two direction
flags, validates that the indices exist, and nothing else: a hem sewn to a neckline is
accepted and counted, an edge sewn to itself is made after a modal dialog, and the
wrong flag pair builds a twisted, bunched seam. This module decides everything that call
does not check *before* it is made, and reads the ledger afterwards to confirm the one
seam that was meant is the one that appeared. Plain Python; ``bridge`` runs it.

Measured on 2026.0.315 (``docs/md-conventions.md``, "Sewing"):

* The direction flags mean "along increasing point index" on that line's contour. Equal
  flags join start to start, opposite flags start to end. Which is right depends on the
  two edges' winding and on the garment, so the caller says which ends meet -- ``ends``
  -- and this never guesses: a guessed direction is how a collar sews, measures right
  on every length, and comes out twisted.
* MD's own Check Sewing Length highlights a difference over 5 mm.
"""

from __future__ import annotations

from typing import Any

from md_mcp import drafting, seams

#: MD's Check Sewing Length threshold. A deliberate ease or gather says so explicitly.
DEFAULT_MAX_DIFFERENCE_MM = 5.0

ENDS = {
    # start of A meets start of B, and end meets end
    "same": (True, True),
    # start of A meets end of B
    "opposite": (True, False),
}


class SewError(ValueError):
    """The seam was not made, and the message says why."""


def parse_ref(ref: str) -> tuple[str, str]:
    """``"piece.edge"`` (or ``"piece.3"`` for a line index) as (piece, edge)."""
    if not isinstance(ref, str) or "." not in ref:
        raise SewError(
            f"{ref!r} must be 'piece.edge' -- a piece name, a dot, and an edge name from "
            "md_draft_piece or a line number"
        )
    piece, edge = ref.rsplit(".", 1)
    if not piece or not edge:
        raise SewError(f"{ref!r} must be 'piece.edge'")
    return piece, edge


def resolve(ref: str, pieces: dict[str, dict], store: dict) -> dict:
    """Turn a reference into a piece index, a line index and that line's span.

    ``pieces`` maps piece names to ``{index, count, points, lengths}`` read from MD now;
    ``store`` is the edge maps kept inside MD. A name used by several pieces, a stored
    map that no longer fits its piece, and a line that does not exist are all refused.
    """
    piece_name, edge = parse_ref(ref)
    piece = pieces.get(piece_name)
    if piece is None:
        raise SewError(f"no piece is named {piece_name!r}; the scene has {sorted(pieces)}")
    if piece.get("count", 1) > 1:
        raise SewError(
            f"{piece.get('count')} pieces are named {piece_name!r}, so the name does not "
            "say which; rename one first"
        )
    lengths = piece["lengths"]
    if edge.isdigit():
        line = int(edge)
        named = None
    else:
        entry = store.get("pieces", {}).get(piece_name)
        if entry is None:
            raise SewError(
                f"{piece_name!r} has no edge names -- it was not drafted with "
                "md_draft_piece, or its map was lost. Name the edge by line number "
                f"instead ({piece_name}.0 to {piece_name}.{len(lengths) - 1})"
            )
        stale = drafting.still_matches(entry, piece["points"])
        if stale:
            raise SewError(
                f"the edge names stored for {piece_name!r} no longer fit it: {stale}. "
                "Use line numbers, or draft the piece again"
            )
        if edge not in entry["edges"]:
            raise SewError(
                f"{piece_name!r} has no edge {edge!r}; its edges are "
                f"{sorted(entry['edges'], key=entry['edges'].get)}"
            )
        line = entry["edges"][edge]
        named = edge
    if not 0 <= line < len(lengths):
        raise SewError(
            f"{piece_name!r} has lines 0 to {len(lengths) - 1}, not {line}"
        )
    start = sum(lengths[:line])
    return {
        "ref": ref,
        "piece": piece_name,
        "piece_index": piece["index"],
        "edge": named,
        "line": line,
        "length_mm": round(lengths[line], 2),
        "span_mm": [round(start, 2), round(start + lengths[line], 2)],
    }


def _sewn_already(side: dict, ledger: dict) -> list[str]:
    """Existing seams that use any stretch of this side's span."""
    low, high = side["span_mm"]
    found = []
    for group in ledger.get("groups", []):
        for pair in group["pairs"]:
            for other in (pair["a"], pair["b"]):
                if other.get("piece") != side["piece"]:
                    continue
                o_low, o_high = sorted((other["from_mm"], other["to_mm"]))
                if min(high, o_high) - max(low, o_low) > seams.TOUCH_MM:
                    found.append(f"{group['name']} ({seams._describe(pair)})")
    return found


def plan(
    a: dict, b: dict, ends: str, ledger: dict,
    max_difference_mm: float = DEFAULT_MAX_DIFFERENCE_MM,
    allow_layered: bool = False,
) -> dict:
    """Decide whether two resolved sides may be sewn, and with which flags."""
    if ends not in ENDS:
        raise SewError(
            f"ends must be 'same' (start of A meets start of B) or 'opposite' (start of "
            f"A meets end of B), not {ends!r}. An edge starts at the corner it was drafted "
            "from and ends at the next corner. Two pieces drafted with the same winding "
            "and meeting face to face usually take 'opposite' -- but this is the "
            "garment's decision, which is why there is no default"
        )
    if a["piece"] == b["piece"] and a["line"] == b["line"]:
        raise SewError(
            f"{a['ref']} and {b['ref']} are the same edge. MD makes that seam after a "
            "modal dialog that stops the bridge until someone dismisses it in MD"
        )
    if a["piece"] == b["piece"]:
        overlap = min(a["span_mm"][1], b["span_mm"][1]) - max(a["span_mm"][0], b["span_mm"][0])
        if overlap > seams.TOUCH_MM:
            raise SewError(f"{a['ref']} and {b['ref']} overlap on the same piece")
    if not allow_layered:
        for side in (a, b):
            used = _sewn_already(side, ledger)
            if used:
                raise SewError(
                    f"{side['ref']} is already sewn: {'; '.join(used[:3])}. Sewing it again "
                    "is a layered seam; pass allow_layered=True only if that is the "
                    "construction (a lining caught into a shell seam, say)"
                )
    difference = abs(a["length_mm"] - b["length_mm"])
    if difference > max_difference_mm:
        raise SewError(
            f"{a['ref']} is {a['length_mm']:.1f} mm and {b['ref']} {b['length_mm']:.1f} mm, "
            f"{difference:.1f} mm apart, over the {max_difference_mm:g} mm allowed. If "
            "that is ease or gathering on purpose, raise max_difference_mm to say so; "
            "otherwise MD spreads the difference along the seam"
        )
    direction_a, direction_b = ENDS[ends]
    return {
        "a": a,
        "b": b,
        "ends": ends,
        "flags": [direction_a, direction_b],
        "args": [a["piece_index"], a["line"], b["piece_index"], b["line"],
                 direction_a, direction_b],
        "length_difference_mm": round(difference, 2),
    }


def _covers(side: dict, planned: dict) -> bool:
    if side.get("piece") != planned["piece"]:
        return False
    low, high = sorted((side["from_mm"], side["to_mm"]))
    p_low, p_high = planned["span_mm"]
    return abs(low - p_low) <= seams.TOUCH_MM and abs(high - p_high) <= seams.TOUCH_MM


def confirm(planned: dict, before: dict, after: dict) -> dict[str, Any]:
    """Check the ledger after sewing holds exactly the planned seam, and nothing else new.

    Returns ``{ok, added, problems}``; ``problems`` explains any way MD's result is not
    the plan. The seam is in MD either way -- there is no call that removes one.
    """
    changed = seams.diff(before, after)
    problems = []
    if changed["removed"]:
        problems.append(f"seams disappeared: {changed['removed']}")
    new_pairs = []
    old_keys = {
        seams._pair_key(pair) for group in before.get("groups", []) for pair in group["pairs"]
    }
    for group in after.get("groups", []):
        for pair in group["pairs"]:
            if seams._pair_key(pair) not in old_keys:
                new_pairs.append(pair)
    if len(new_pairs) != 1:
        problems.append(f"{len(new_pairs)} new seam pairs appeared where one was planned")
    else:
        pair = new_pairs[0]
        matched = (
            _covers(pair["a"], planned["a"]) and _covers(pair["b"], planned["b"])
        ) or (_covers(pair["a"], planned["b"]) and _covers(pair["b"], planned["a"]))
        if not matched:
            problems.append(
                f"the new seam joins {seams._describe(pair)}, not "
                f"{planned['a']['ref']} and {planned['b']['ref']}"
            )
    new_warnings = {
        kind: [m for m in messages if m not in before.get("warnings", {}).get(kind, [])]
        for kind, messages in after.get("warnings", {}).items()
        if kind in ("self", "reused", "count", "unknown_piece")
    }
    new_warnings = {kind: m for kind, m in new_warnings.items() if m}
    if new_warnings:
        problems.append(f"the ledger has new warnings: {new_warnings}")
    return {"ok": not problems, "added": changed["added"], "problems": problems}
