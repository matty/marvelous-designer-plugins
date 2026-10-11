# Session report: a vampire cape for Nardo, 2026-10-11

MD 2026.0.315 Enterprise Offline, driven through md-mcp (`md_status`, `md_api`,
`md_call`, `run_md_python`) from Claude Code. Avatar: Nardo, a tailed T-pose avatar.

**Outcome: stopped by the user, and the result judged unusable.** In the user's words:
"you are not correctly wiring up the seams, its awful and really bad. I don't think the MD
MCP works well here and you cannot design real garments with it." The scene was left
open and unsaved; `Desktop\Vampire_Cloak.zprj` still holds only the avatar.

The point of this report is the list of failures, not the cape. Improvements are in
[../roadmap.md](../roadmap.md).

## What was asked

"Create a vampire cape / cloak for the current avatar." Mid-session the user supplied a
reference: a commercial cape sewing pattern (Made to Modify) showing a long black cape,
a moderate pointed stand collar, red lining throughout, continuous over the shoulders.
Asked how to treat the T-pose arms, the user chose arm slits.

## What was left in the scene

8 pieces, 40 seam groups, 3 added fabrics:

| Piece | Fabric | Notes |
|---|---|---|
| `Cape_Back` | Cape Velvet Black (`V2_Woven_Velvet_Velveteen_1`) | one piece, CB vent for the tail |
| `Cape_Front_L`, `Cape_Front_R` | velvet | joined at the throat by a 60 mm "clasp" seam |
| `Cape_Collar` | Collar Leather Black (`V2_Leather_Full_Grain`) | 38° flare stand collar, pointed tips |
| `*_Lining` ×4 | Lining Satin Red (`V2_Woven_Satin_1`) | `LayerClonePatternPieceMove(..., True)` copies |

Six seams were placed deliberately (two shoulders, clasp, collar to the three necklines).
The other 34 were created by MD itself when the linings were layer-cloned.

## Timeline

1. Read the avatar by exporting it to OBJ and slicing it: 2019 mm including ears,
   shoulders at y ≈ 1515, neck base radius ≈ 65–85 mm, back surface at z ≈ −116, tail
   leaving the body at y ≈ 890–1100 and reaching z = −994 at y ≈ 270.
2. Probed 17 arrangement points (create a piece, arrange, export OBJ, delete). The body
   points sit on volumes inflated by the tail: `Body_Back_Center_1` lands at z ≈ −1150.
3. Tried to offset a piece through `ExportPatternJSON`/`ImportPatternJSON`
   (`ArrangementPointDataMap.fOffSetY`). Import dropped the offset and duplicated the
   fabric.
4. **Attempt 1:** full-height back at `Body_Back_Center_1`, fronts at
   `Body_Front_Center_1/3`, sewn, simulated with gravity 0. The back was dragged about
   1 m across the body and the fronts crossed over. Deleted.
5. **Attempt 2:** back drafted as a 200 mm yoke, arranged `Flat` at `Neck_Back`,
   **frozen** while the fronts were pulled onto it, unfrozen, then lengthened to full
   length in 8 `MovePatternPoint` steps with simulation between. The tail came through the
   vent. The drape was plausible; the construction was not real pattern making.
6. Collar v1 (250 mm, 21° flare, a guess at a "Dracula" collar) wrapped round the head.
   Deleted.
7. The user sent the reference image. Collar v2 was created, and **MD placed the new piece
   about 1544 mm up the 2D board**. The growth step sent absolute coordinates, flung a
   tip point ~1.7 m, and MD showed the user "pattern boundary is intersected while
   meshing". `run_md_python` reported success. Deleted and rebuilt with moves relative to
   a read-back reference point.
8. Lining by `LayerClonePatternPieceMove(i, dx, dy, True)` on each piece. MD sewed every
   outline edge of each clone to its original: seam groups went from 6 to 40.
9. One side of the collar caught on the jaw. A shrink-and-regrow pass was running when
   the user stopped the session.

## What went wrong: process (the model's failures)

1. **The repo's own guidance was never read.** `docs/garment-preflight.md` already says:
   save a checkpoint first, keep a seam ledger, start from separated, correctly oriented
   panels near the body, don't hide problems by freezing, avoid point-edit loops that
   remesh after every move, and use read-back coordinates because 2D positions shift.
   This session broke every one of those rules. Nothing in the MCP server's instructions
   points at that document, and the `md-mcp-reliability` skill it mentions was not
   available in the session.
2. **No checkpoint.** MD has no undo; every mistake was permanent until a piece was
   deleted, and deleting renumbers everything.
3. **The 2D board was never laid out.** Every piece was drafted round the origin, so
   pieces and seam lines are stacked over each other. A pattern maker reads the 2D window
   first; it was unreadable.
4. **Construction by workaround.** Short yokes, gravity toggling, freezing and an 8-step
   lengthening loop got cloth onto the body. They did not produce a garment built the way
   it would be cut and sewn. Each step re-meshed. Hem curves, whose interior points can't
   be moved directly, drifted by interpolation (about 2% short mid-hem).
5. **The lining was sewn by accident, not by design.** MD's automatic seams from the layer
   clone were accepted wholesale: each lining piece is bonded to its shell on every edge,
   shoulders, neckline and clasp included. There are no lining-to-lining shoulder seams and
   no ledger of what joins what. That is most of the "seams are wrong" the user saw.
6. **Designed before seeing a reference.** The first collar was a guess. The user had to
   supply a reference before the design converged, and the arm question came only then.
7. **A wrong fact went out before it was checked.** `MovePatternPoint` was reported to a
   documentation subagent as a relative move. It is absolute; the "relative" reading was a
   coincidence of a point at the origin. Corrected before the note landed.
8. **Too much mutation between check-ins, and split attention.** Many build steps ran
   between images shown to the user. Two repo subagents were started in the middle of the
   build.

## What went wrong: the bridge and MD's API

1. **No way to place a piece in 3D.** Only arrangement points; `SetArrangementPosition`
   is a no-op; JSON offsets are discarded; no transform setter exists. On a tailed avatar
   the points are up to 1 m away from the body. *(Corrected later the same day:
   `SetArrangementPosition` does move a piece round, along and out from its point's
   bounding volume, once the piece is re-placed; see md-conventions → Arrangement. Still
   no world transform.)*
2. **No geometry readback.** `GetClothPositions` cannot return data to Python. Every
   position in this session came from exporting an OBJ and parsing it.
3. **No structured seam readback.** The only view of what is sewn to what was
   `ExportPatternJSON`, where seams reference outline IDs and perimeter fractions. That
   took decoding by hand mid-session.
4. **No call removes a seam.** `md_api`'s full index lists `AddSeamlinePairGroup` and
   getters only. MD's automatic layer-clone seams could only be removed by deleting the
   piece.
5. **MD's errors don't reach the caller.** The meshing error appeared in MD's window; the
   script that caused it returned `execution_succeeded: true`.
6. **New pieces land at unpredictable 2D positions.** The first and third collars
   appeared where drafted; the second appeared about 1544 mm up the board. Nothing reports
   the shift.
7. **Silent no-ops everywhere.** `MovePatternPoint` silently refuses degenerate moves,
   `ExportOBJ` into a missing folder returns paths and writes nothing, and `Simulate(n)`
   is always `True`. Each needs a read-back.
8. **Materials can't be specified.** There are no fabric physics setters, so stiffness
   comes only from picking a preset `.zfab` blind. Writing a back-side colour (side 1) is
   recorded as crashing MD, so a lining had to be built as extra pieces.
9. **Seeing the result is expensive.** Each check took an MD snapshot, a file write, an
   ffmpeg tile and a file read.

## Measured this session, not yet in md-conventions or notes

All on 2026.0.315, 2026-10-11. Fold these in once re-checked.

- **`LayerClonePatternPieceMove(i, dx, dy, True)`** appends the clone at index
  `count − 1`, named `<name>_1`, with identical line lengths, offset by `(dx, dy)` on the
  2D board and placed a few mm inside in 3D. It also **creates one seam group per outline
  line** between clone and original (13 for the collar, 9 for the back, 6 per front). Each
  has `bIsTurned: true`, `FoldData.iAngle: 180`, `Direction` True/True, and together they
  cover the whole outline.
- **Seams in pattern JSON** reference the piece's outline `ShapeID` with `LengthParam`
  `fStart`/`fEnd` as fractions of the outline perimeter. The back's left shoulder read
  0.784–0.807, matching 5445/6944 – 5605/6944 mm computed from line lengths. Seams kept
  tracking their lines after `MovePatternPoint` lengthened the pieces.
- **`SetCamViewPoint`** by eye: 0 bottom, 1 three-quarter, 2 front, 3 three-quarter,
  4 side, 5 top, 6 side, 7 looked the same as 6, **8 and 9 back**. This contradicts the
  conventions doc's "0..7 … round the back" (measured 2026-09-19). Re-check. *(Re-checked
  later the same day and folded into md-conventions: 8 is the back; 7 and 9 zoom without
  turning, so 9 showed the back only because it followed 8.)*
- **Back pieces mirror, front pieces don't.** A piece arranged on a `Body_Back_*` point
  maps 2D `+x` to the avatar's right (3D −x); on a `Body_Front_*` point 2D `+x` is the
  avatar's left (3D +x). Measured with an asymmetric spike piece on `Body_Back_1_L/R` and
  `Body_Front_1_L/R`. `Body_Back_1_*` and `Body_Front_1_*` face sideways, not back or
  front.
- **`SetArrangementShapeStyle(i, "Flat")` at `Neck_Back`** gave a tilted plane (654 ×
  215 mm piece: y 1512–1711, z −194..−115) with no avatar contact. That was the one clean
  placement found behind the neck.
- **`SetPatternFreeze(i, True)`** held the frozen piece exactly still while sewn
  neighbours were pulled onto it; unfreezing let it drop normally.
- **`SetPatternStrengthen`** tints the piece tan in the 3D viewport (layer stayed 0). A
  two-layer leather and satin collar stood without it.
- **`ExportSnapshot2D(path, 0)`** returned `[]`.
- **`.zfab`** is a zip holding a binary `.fab` plus texture maps. The `.fab` carries keys
  such as `fDensity`, `fFriction`, `fThickness`, `fBucklingStiffnessU/V/H` and shear
  constants, so presets can be compared offline once the format is decoded.
