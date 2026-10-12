# How Marvelous Designer behaves through md-mcp

What has been measured about MD 2026.0.315, kept short. Every point is backed by
`docs/md-conventions.md` in the md-mcp repo. Nothing here says how to design a garment.

## workflow

1. `md_status` first: it says which listener build answered, what the scene holds, and
   whether MD is showing a dialog. MD keeps old plugin paths for good, so check the build.
2. `md_api` before any call: it gives the exact spelling and arity (MD picks overloads by
   arity) and a measured note where one exists. Read the note.
3. `md_checkpoint` before anything you may want back. MD's API has no undo, and nothing
   removes a seam; `md_restore` is the only way back.
4. Read the scene back after every step. Return values are not evidence (see `readback`).
5. After sewing, `md_seams`. After draping, `md_fit` and `md_snapshot(views=[2, 4, 8])`;
   `md_snapshot(pattern=True)` shows the 2D pattern.
6. If a reply mentions a dialog, look at MD: nothing reaches MD until it is dismissed, and
   the call that raised it may not have done what its reply said.

Lengths are millimetres. In 2D, x is right and y is up.

## building

- `md_draft_piece` builds a piece from named edges: corners in order, each starting an
  edge, curve points under `through`. Edge i is MD's line i, so a point sewn to two
  pieces must be a corner. It refuses outlines MD would build wrong, keeps pieces apart
  on the 2D board, and checks MD's corners and lengths before storing the names.
- `md_sew("front.side", "back.centre", ends="opposite")` sews by name. `ends` has no
  default: the wrong one builds a twisted seam MD accepts. It refuses an edge sewn to
  itself or twice, and lengths over 5 mm apart unless raised, then checks the ledger.
- Edge names travel in `md_checkpoint`; `md_edges` lists them and flags a reshaped piece.

## readback

- `utility_api.Simulate(n)` runs n solver steps and returns True for any n. One step
  leaves the cloth flat; steps add up across calls, and a few hundred is a drape.
- `AddSeamlinePairGroup` returns False only for an index that does not exist. It accepts
  any two real edges, even a hem joined to a neckline, and it makes an edge-to-itself
  seam after showing a dialog. Count and inspect with `md_seams`.
- An export can return paths it never wrote. md-mcp checks every `Export*` call:
  `missing_outputs` means MD claimed a file that is not there.
- `fabric_api.GetFabricCount()` answers 0 for a scene with a fabric in it; the scene
  readback uses `GetFabricStyleNameList()`.
- `SetViewPoint` moves nothing; `SetCamViewPoint` is the camera call (0 bottom,
  2 front, 4 right, 6 left, 8 back; 7 and 9 zoom without turning).

## seams

- An MD line runs from one straight point to the next, taking in every curve point
  between. A curve sampled as 50 points is one line, and line numbers count lines.
- `md_seams` names, for each seam, the piece, the span along its outline in mm, and the
  line numbers it covers, and warns on an edge sewn twice or to itself.
- Layer Clone sews every edge of the clone to its original, on its own. Nothing removes
  a seam, so a seam you did not want means `md_restore`.
- Sewing an edge to itself raises a modal dialog, and the seam is made anyway.

## placement

- `SetArrangement(piece, point)` puts the centre of the piece's 2D box on a point. Names
  are not positions: on a tailed avatar body points sat a metre off the body.
- No points? `import_api.ImportFile` MD's `Preset/Avatar/Arrangement/Adult_V2_Arrangement_
  Bounding_Volume.pan`, then `Adult_V2_Arrangement_Point.arr`: Genesis 8 gets 109.
- `SetArrangementPosition` x, y, offset (0-100) are coordinates on the point's volume.
  `md_place` searches them to a height and a clearance, clear of pieces it placed before.
- Prefer "Flat". "Curved" started a skirt inside the hips and a seam caught open.
- Place sleeves before the body: placed after, they were pushed out to clear the back
  and their underarm seams closed through the arm.

## fitting

- `md_body`: heights, girths and front/back arcs at waist, hip and bust, and the
  shoulder line. Draft fronts and backs to their arcs: Genesis 8's bust is 560 mm in
  front and 378 behind, and fronts cut to a quarter were pulled apart.
- `md_fit` (`draped=True` after a drape): cloth in the body, pieces through each other,
  open seams; `rings` with stretch and ease against the body in each ring's own plane;
  `gaps` between unsewn edges. A ring stretched over 5 % is carrying load.
- A seam path crossing an arm is usually fine; the body through the cloth is not.
- Measured on a jacket: an armhole needs ease over the arm root (none: 12 % stretch, the
  arm through the sleeve); a sleeve's underarm seam is the arm's length, its cap on top;
  open fronts swing into the arms unless a short tack holds them.
- A plain waist slides ~10 %; `SetPatternPieceElastic` held a skirt at the waist.

## saving

- Saving a `.zprj` copy (`ExportZPrj`, every form) makes MD treat the copy as its open
  project: the user's next Save goes there. `md_checkpoint` saves a `.zpac` instead, which
  leaves the project alone.
- A `.zpac` is the garment without the avatar; `ImportZpac` replaces the garment and keeps
  the open avatar. `ImportZprj` replaces everything.
- Saving any copy clears MD's unsaved-changes flag, so MD would close without asking.
  `md_checkpoint` and `md_restore` set it again.
- `NewProject()` discards the scene without asking.

## simulation

- Particle distance: a coarse one on narrow pieces raised meshing errors (welts about
  4 mm wide failed at 5 mm). Narrow trims need a fine particle distance.
- `MovePatternPoint` is an absolute move, not a relative one, and silently refuses a
  degenerate move. A point flung far enough to make the outline cross itself raised
  "Pattern Boundary is intersected while meshing" in MD's window while the script
  reported success. Move points relative to where they read back as being.
