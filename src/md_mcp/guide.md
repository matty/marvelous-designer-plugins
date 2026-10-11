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
5. After sewing, `md_seams`. After moving or draping, `md_measure` and
   `md_snapshot(views=[2, 4, 8])` for front, side and back.
6. If a reply mentions a dialog, look at MD: nothing reaches MD until it is dismissed, and
   the call that raised it may not have done what its reply said.

Lengths are millimetres. In 2D, x is right and y is up.

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

- `SetArrangement(piece, point)` puts the centre of the piece's 2D bounding box on an
  arrangement point. A point's name is not its position: on an avatar with a tail, body
  points sat about a metre off the body. `md_arrangement_points` finds where they are.
- `SetArrangementPosition(piece, x, y, offset)`, each 0 to 100, moves the piece round the
  point's bounding volume, along it, and out from it -- but only once the piece is
  re-placed: call `SetArrangementOrientation(piece, <its current orientation>)` after it.
  `ResetClothArrangement()` re-places every piece and loses any drape.
- `SetArrangementOrientation` is in degrees, and the default is 180.
- There is no call that moves a piece freely in 3D. Read where it landed with
  `md_measure`.

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
