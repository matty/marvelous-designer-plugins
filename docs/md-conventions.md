# How Marvelous Designer actually behaves

Measured against **MD 2026.0.315** unless a line says
otherwise. Everything here was learned by building scenes in MD and reading them back.
Measurements below apply to the tested cases, not every topology, pose or MD build.
For recommended construction checks, read [garment-preflight.md](garment-preflight.md).

Where a measurement contradicts MD's published documentation, the measurement wins and
the contradiction is noted. `md_api` reads the running build rather than any captured
list, and a five-line probe against the live build settles anything here that matters.

Ordered by the work: drafting, sewing, simulating, dressing, finishing, then the API's
own quirks and the environment it runs in.

---

## Units and coordinates

**Millimetres, always.** MD's UI unit setting is a display preference and the API
ignores it: a 300 mm square created with the UI in centimetres reads back `300.0` from
`GetLineLength`, and `ExportPatternJSON` states `"Unit" : "mm"`.

In 2D pattern space `x` is right and `y` is **up**, and coordinates come back exactly as
sent. The origin is the 2D pattern window's origin; coordinates given to
`CreatePatternWithPoints` are absolute in that world, and `SetPatternPiecePos` moves a
piece within it. **`GetPatternPiecePos` answers the centre of the piece's 2D bounding
box**, not the point it was drawn from: `[100, 50]` for a 200 × 100 rectangle drawn from
the origin (2026-10-11, 2026.0.315).

**In 3D it is millimetres again, `y` up, and `+z` is the avatar's front** — read from an
`ExportOBJ` of an avatar on 2026-10-11. Arranging a piece maps its 2D `+y` to 3D up: a
piece drafted with its tip at `y = 600` came out tip uppermost.

**Winding is not enforced.** Clockwise and counter-clockwise are both accepted, with
areas agreeing to four significant figures.

Paths use forward slashes even on Windows, and should be absolute.

---

## Drafting a piece

`CreatePatternWithPoints(_points)` takes `list[tuple[float, float, int]]`, the `int`
being the vertex type:

| Value | Meaning |
|---|---|
| `0` | Straight point (a corner) |
| `2` | Spline curve point |
| `3` | Bezier curve point |

`1` is undocumented — do not send it. The type belongs to the **point**, not to the
segment leaving it: a run of type-`2` points is a smooth spline through them, so a
neckline is corner → spline run → corner.

**Type `3` is unusable in practice.** MD exposes no way to set tangent handles, so a
bezier point's shape cannot be controlled. Use spline points.

**The contour is implicitly closed.** Do not repeat the first point as the last — a
4-point square has 4 lines, not 5, and repeating the point produces a contour whose
lines cannot be addressed at all.

`_isClosed` on `CreateInternalShapeWithPoints` is explicit because internal shapes are
commonly open (a fold line, a dart leg guide).

### Splines run longer than the chords through them

MD interpolates close to the true arc, so the sampled curves in this probe measured **long**
against the straight lines between its own points, and how much depends on density:

| Interior spline points | MD over the chords |
|---|---|
| 1 | **+1.23 %** |
| 3 | +0.51 % |
| 4 | +0.34 % |
| 6 | +0.18 % |
| 24 | +0.01 % |

Against the *true* arc MD is within 0.07 % from four interior points on. So MD measures
the curve, and anything comparing chord sums is measuring something else. Four interior
points recovered this probe's shape adequately. Other curves need their own length
and shape checks; this is not a universal minimum.

A chord-length-parameterized natural cubic spline reproduces MD's recorded lengths to
better than 0.01 mm across these densities, if you need to predict a length before
building. `md_draft_piece` predicts every edge this way and compares MD's figure after
building: a 400 mm edge bulging 55 mm through three spline points was predicted at
418.53 mm and measured 418.52 mm (2026-10-12, 2026.0.315).

### Moving a point

**`pattern_api.MovePatternPoint(piece, point, x, y)` is an absolute move.** It puts the
point *at* `(x, y)`: on a rectangle `(0,0), (300,0), (300,200), (0,200)`,
`MovePatternPoint(piece, 1, 50, 0)` took point 1 from `(300, 0)` to `(50, 0)`. On a point
sitting at the origin it looks relative — `(0, -100)` sent to a point at `(0, 0)` reads as
a delta — which is how it was first misread. Measured 2026-10-11, 2026.0.315.

`point` counts **straight points only**: the ones `GetPatternInputInformation`'s
`PointList` reports, which is the same numbering lines use (below).

**A move that would leave the outline degenerate is refused without a word.** Point 2 of
that rectangle sent to `(0, 100)`, on the edge between points 3 and 0, and point 1 of
another piece sent to `(0, -100)`, on top of a point 0 just moved there, both returned
`None` and left the piece exactly as it was. A refusal and a move look the same from Python, so read the
outline back after every move.

**Moving a straight point carries the interior of an adjacent curved line with it**, with
a linear falloff along the line. A corner moved from `(0, 0)` to `(0, -100)`, at one end
of a curved line running to `(400, 0)`, took the curve points `(100, -30)`, `(200, -40)`,
`(300, -30)` to `(100, -104.5)`, `(200, -90)`, `(300, -55.5)` — shifts of 74.5, 50 and
25.5 mm. Measured from the new chord the bulge is `-29.5`, `-40`, `-30.5`, against `-30`,
`-40`, `-30` before: the curve is carried along, not bent at the corner.

---

## The four index spaces

Confusing them is the likeliest way to build something silently wrong.

**1. Pattern index** — returned by `CreatePatternWithPoints`, `CopyPatternPiecePos`,
`CopyPatternPieceMove`. Scene-global, dense, 0-based, in creation order.
`GetPatternCount()` is the count — **not** `GetPatternSize()`, which returned 0 for a
three-piece scene despite an identical docstring.

`DeletePatternPiece` **renumbers the survivors**: delete index 1 of three and the piece
above slides into it, so an index is stable only while nothing is deleted. Re-read every
index after any deletion. `utility_api.NewProject()` resets the numbering, and the next
create returns 0.

**2. Inner-shape index (`_childIndex`)** — returned by
`CreateInternalShapeWithPoints`, scoped **to the parent pattern**, not global. The second
pattern's shapes number from 0 again. Its docstring says "@return PatternIndex"; that is
a copy-paste error.

**3. Line index** — an edge of a pattern or of an inner shape, and **never returned by
any call**. You have to derive it.

**A line runs from one straight point to the next**, absorbing any curve points between
them. The number of addressable lines equals the number of type-`0` points, so an
armscye sampled at eight points is **one** line, not seven. For an all-straight contour
this reduces to the obvious reading: line `i` runs from point `i` to `i+1`, with the last
closing back to point `0` (measured again 2026-10-11). Two consequences:

- Any point that must be sewn to two *different* counterparts has to be a straight point.
  A sleeve's cap point is the example: smooth it and the whole cap becomes one line, so
  only one of the two armscyes can attach.
- A contour with no straight points at all is a single closed curve.

**4. Seamline pair group index** — `GetSeamlinePairGroupCount()`,
`GetSeamlinePairGroupListInPattern(_patternIndex)`. `AddSeamlinePairGroup` is declared
`-> bool` and its `@brief` claims it returns the group name; it returns `True`. Treat the
return as opaque and read the index back when you need it.

---

## Sewing

### What MD checks, and what it does not

**`AddSeamlinePairGroup` returns `False` when it names a line or a piece that does not
exist**, and does not make the group. Ten cases, each agreeing with the seam group count:
one past the end, far out of range, `-1`, another negative, a pattern index with no
piece, and a line index into the curve points an MD line absorbs — all `False`, none
made. All three overloads behave the same way.

**But it validates indices, not meaning.** A seam joining the front's hem to the back's
neckline is accepted, counted, and wrong. So `False` is a reliable "that index is
nonsense", and `True` is *not* "that seam is right". Count seam groups and read the scene
back; a garment that measures correctly can still be sewn inside out, and the sharpest
case on record is a collar that sewed, matched on every length, and was twisted.

**A degenerate seam raises a modal dialog.** Sewing an edge to itself produced "There are
some abnormal seamlines in the file (marked as red). * There may be overlapped
seamlines." with an OK button — and a modal dialog holds MD's main thread, which is the
thread serving the bridge. Everything stops until somebody dismisses it, and resumes when
they do. Nothing times out at MD's end, so a bridge call that never returns is worth
*looking at MD* for before it is worth debugging.

**And the seam is made anyway.** Measured 2026-10-11 on 2026.0.315: once the dialog was
dismissed, `AddSeamlinePairGroup` returned `True` and the seam group was counted, so the
reply alone reads as success. `md_seams` reports it ("sewn to an overlapping part of
itself").

**The dialog can be seen, and closed, from outside MD** (same day, same dialog):

| | |
| --- | --- |
| Win32 | A visible top-level window, class `Qt6103QWindow`, **owned by MD's main window** (`Qt6103QWindowIcon`) and titled the same; the main window is **disabled** while it is up and enabled once it closes |
| UI Automation | Only an "Information" label and an "OK" button — **not the message text** |
| Dismissal | Invoking that OK button through UI Automation from another process closed the dialog, and the blocked request finished |

md-mcp uses the first row: a request waiting on MD looks once a second, and abandons the
reply (not the work) once a dialog has been up for two looks — measured giving up after
2.0 s where it would have waited 120 — and `run_md_python`, `md_call` and `md_status`
report a dialog showing after a reply. It names the dialog and cannot quote it. It does
not dismiss one; the live test does, to clean up after itself.

### Seams can be read back per piece, not just counted

`GetSeamlinePairGroupListInPattern(_pattern)` returns the seam group ids that touch one
piece. That is more than `GetSeamlinePairGroupCount()` gives, and it is the closest thing
to checking a seam rather than trusting it:

* **A piece with an empty list is sewn to nothing.** For a piece intended to be
  attached, this is a review flag the count alone cannot show — the total goes up whether or not the piece it was
  meant for was involved.
* **Intentionally symmetric constructions should have equal counts.** Comparing every `*_L` against its `*_R`
  turns an asymmetric build into one number that disagrees.

Measured 2026-09-19 on a 56-piece garment (2026.0.315): 133 groups reported, 133 distinct
ids seen across the pieces, ids `0..132`, no piece raised, no orphans, and all 20
mirrored pairs equal. The ids are group indices in the same space as
`GetSeamlinePairGroupCount`, so every id returned is `< count`.

It still does not tell you the seam joined the *right* edges — nothing does. It tells you
which pieces were involved, which catches the common build errors.

### Direction

`AddSeamlinePairGroup(..., _directionA : bool, _directionB : bool)` — "true : forward,
false : backward", meaning *along increasing point index* on that line's contour. Two
edges sewn with the same flag join start-to-start; opposite flags join start-to-end.

Two counter-clockwise pieces meeting face to face traverse their shared seam in opposite
directions, so the pair in this measured setup is `(True, False)`. Derive the correspondence
again for flipped or imported contours; this is not a universal default.

**Measured, because nothing else settles it.** Both pairs are *accepted*, and cloth with
nothing to hang on falls in a heap whichever way it is sewn. Sewn around an avatar's limb
and simulated, `(True, False)` produces a tube spanning the panel's full 600 mm at a
radius varying 17 mm about 85; `(True, True)` collapses to a 238 mm bunched mass with the
radius swinging 24 → 78 → 29.

---

## Simulation

**`utility_api.Simulate(n)` runs `n` solver steps.** It is not "simulate until settled",
and it returns `True` for every `n`, including ones that do nothing. Measured on two sewn
360 × 500 mm panels, which start flat in a plane — so any z spread at all is the solver
having run:

| Call | Wall time | z spread after |
|---|---|---|
| `Simulate(0)` | 0.01 s | 0.00 mm |
| `Simulate(1)` | 0.05 s | ~0 mm |
| `Simulate(10)` | 0.15 s | 100 mm |
| `Simulate(100)` | 0.46 s | 328 mm |
| `Simulate(1000)` | 3.54 s | 433 mm |

Two things follow. **`Simulate(1)` returns `True` and leaves the garment flat** — a
caller that reads that `True` as "simulated" exports a flat sheet, and MD never says
otherwise. And **steps accumulate**: each call continues from where the last one finished
rather than re-solving, so a loop of `Simulate(1)` buys the same drape for roughly twenty
times the wall time.

**The drape settles, and the spread is not monotonic.** Stepping 100 at a time, measuring
how far the most-displaced vertex moved since the previous export:

| Total steps | z spread | Furthest vertex moved since last |
|---|---|---|
| 100 | 327 mm | 1048 mm |
| 200 | 355 mm | 286 mm |
| 400 | 427 mm | 63 mm |
| 600 | 426 mm | 9.6 mm |
| 800 | 427 mm | 0.3 mm |

So a few hundred steps is a drape and the rest is settling — and a later export can
measure *smaller* than an earlier one. Anything checking progress by comparing a spread
to the one before it will fail at random; compare vertex positions, or simulate enough
and look.

One run of one scene, and **indicative rather than a contract**: this cloth had no
arrangement point to hang from, so it free-falls and tumbles, and past the first few
hundred steps the numbers wander. Take the shape of the curve — fast, then slow, then
settled — and not the figures.

### Layers hold trims off the garment, including after they have settled

`SetPatternLayer(_pattern, n)` with `n > 0` makes a piece simulate *outside* everything
below it. That is what gets a pocket or a strap to drape on top of the shell instead of
through it, and during a build it is the right tool.

**It is not a finishing state.** A garment whose 46 trim pieces were left on layers 1-4
had every pocket, flap, strap and buckle sitting visibly proud of the shell, which reads
as "the trims are misaligned" rather than as a layer setting. Collapsing them all to
layer `0` and simulating a few hundred steps seated them flush, and changed nothing else
(measured 2026-09-19, 2026.0.315).

**The 3D window colour-codes by layer**, so this also reads as a material bug: pieces on
layers 1-2 drew bright lime and those on layers 3-4 drew tan, whatever fabric they were
assigned, while layer 0 drew true fabric colour. Chasing that as a fabric problem is
wasted time — check `GetPatternLayer` first. `Set3DGarmentRenderingStyle` does not turn
the coding off; `0`, `1` and `2` all showed it.

---

### Settings

`SetParticleDistanceOfPattern(_pattern, _mm)` round-trips through
`GetParticleDistanceOfPattern`. So does `SetAddlThicknessCollision` — but its reader is
spelled **`GetAddlThicknessCollisionValue`**, and since `GetAddlThicknessCollision` (the
name anyone would search for) does not exist, the setter reads as write-only when it is
not. It answered `2.5` and `1.0` for two pieces of the same garment, so it is real
per-piece state and not a constant (measured 2026-09-19, 2026.0.315). `SetPatternFreeze`
is the one with no reader anywhere in the six modules.

**`SetSimulationQuality` takes two ints, not one**, matching `GetSimulationQuality()`,
which returns a pair. One argument raises `TypeError: incompatible function arguments` —
loudly, which is the exception rather than the rule in this API.

---

## Avatars

`import_api.ImportAvatar(_path, _option)` needs an `ApiTypes.ImportExportOption()` as its
second argument; there is no one-argument form. It returns `True`, and the avatar then
appears in **`export_api.GetAvatarNameList()`** — the avatar readbacks live on
`export_api`, despite the module's name, and so does `GetAvatarCount`.

Enterprise Network Offline ships no Library assets, but it does ship avatars under
`Preset/Avatar/Converting/{Female,Male}/Genesis{8,9}_*.avt`, which import fine.

### Arrangement

Placement against a body is `SetArrangement(_patternIndex, _arrangementIndex)`, naming a
point from `GetArrangementList()`.

**`SetArrangementPosition(piece, x, y, offset)` works, but only once the piece is
re-placed.** The three values are the GUI's arrangement Position X, Position Y and
Offset, each 0–100; they land in `GetArrangementOfPattern`'s `ArrangementOffsetX/Y/Z`,
and 150 is refused with the old value kept. On their own they move nothing — which is
why an earlier sweep here read every vertex unchanged and called the call a no-op. They
take effect when the piece is re-placed: `SetArrangementOrientation(piece, <its current
orientation>)` re-places that piece alone, `SetArrangementShapeStyle` with its current
style does the same, and `ResetClothArrangement()` re-places **every** piece, drape and
all. `SetArrangement` afterwards puts the point's defaults back. Measured 2026-10-11 on
2026.0.315 with `Nardo`, a 200 × 140 mm probe, centre of its exported bounding box (mm):

| Point | Values | Centre `x, y, z` | Moved |
| --- | --- | --- | --- |
| `Neck_Back` | defaults 50, 50, 50 | 0.6, 1605.4, −146.7 | — |
| `Neck_Back` | x 0 (and x 100) | 0.6, 1491.0, 132.9 | round to the front of the neck |
| `Neck_Back` | y 0 / y 100 | y 1591.5 / 1619.3 | 28 mm along the neck |
| `Neck_Back` | offset 0 / offset 100 | z −38.2 / −245.0 | 207 mm out from it |
| `Shoulder_L` | defaults 45, 50, 50 | 195.0, 1492.9, −8.4 | — |
| `Shoulder_L` | y 0 / y 100 | z 69.1 / −85.9 | 155 mm front to back |
| `Shoulder_L` | offset 0 / offset 100 | y 1386.1 / 1595.2 | 209 mm |

So `x` runs round the bounding volume, `y` along it, and the offset is distance from it
— about 2 mm a unit on this avatar. The units are the volume's, not millimetres, so read
where the piece landed rather than predicting it.

**The three values are coordinates on the volume, not offsets from the point.** Measured
2026-10-12 on 2026.0.315 with Genesis 8 Female and MD's Adult V2 points (see below): a
300 mm square arranged at `Body_Front_Center_5` and at `Body_Back_Center_5` landed in
the same place for the same values — the point only supplies the defaults
(`ArrangementOffsetX/Y/Z` in its `GetArrangementList()` entry, 50/15/50 and 0/15/50
there). On the body volume, centre of the piece (mm):

| Value | 0 | 25 | 50 | 100 |
| --- | --- | --- | --- | --- |
| `x` (round) | back centre, z −173 | avatar's left side, x +178 | front centre, z +127 | back centre |
| `y` (along) | y 888 | | y 1178 | y 1467 |
| offset (out) | z 27 — inside the body | | z 127 | z 227 |

About 5.8 mm a unit along and exactly 2 mm a unit out. That makes placement a search over
three bounded integers with a measurable result, which is what `md_place` does.

`SetArrangementOrientation`'s `_orientation` is **degrees**, and the default is 180, not
0. `SetArrangementShapeStyle` accepts only `"Flat"` and `"Curved"`.

**Arranging puts the centre of the piece's 2D bounding box on the point**, with 2D `+y`
up — the same centre `GetPatternPiecePos` reports. A 600 mm tall piece on
`Body_Back_Center_1` spanned `y` 1145–1745 (2026-10-11, 2026.0.315). So a long piece
reaches as far above its point as below it.

**`GetArrangementList()` can be empty, and then there is nowhere to arrange to.** The
shipped `Genesis8_Female.avt` loads with no arrangement points, and
`ImportExportOption.bAddArrangementPoints = True` does not add any — measured, the list
stays `[]`. `SetArrangement(piece, 0)` then does nothing, while `GetArrangementOfPattern`
still answers with a full-looking record (`{'ArrangementName': 'Arrangement Point',
'ArrangementOffsetX': '50', ...}`), so the readback does not reveal it either.
Without them a garment can still be drafted, sewn, simulated and exported — it simply
falls rather than draping on the body.

**MD ships the points, and they can be added.** An earlier version of this document said
arrangement points come only with Library avatars; that was wrong. Measured 2026-10-12 on
2026.0.315: after `ImportAvatar` of `Genesis8_Female.avt`,
`import_api.ImportFile(".../Preset/Avatar/Arrangement/Adult_V2_Arrangement_Bounding_Volume.pan")`
(returns `True`, the list stays empty) and then
`ImportFile(".../Adult_V2_Arrangement_Point.arr")` gave **109 points**, and they sit on
this body: `Body_Front_Center_1` landed a probe at z +122 by a chest front at +134,
`Body_Back_Center_1` at −149, `Body_Front_Center_5` at y 972 and `Shoulder_L` at
x 129, y 1492. The folder also holds `Adult_V1_*` and `Kid_*` sets.

**An imported avatar can carry them too.** The `Nardo` avatar imported from `.obj`
reported 109 named points (`Arm_Back_1_L`, each with offsets, orientation and a
`Curved`/`Flat` type) on 2026-09-19, 2026.0.315. Read `GetArrangementList()` rather than
assuming either way.

### A point's name is not its position

Arrangement points sit on bounding volumes sized by the **whole** avatar, so on an avatar
with a long tail the body points are nowhere near the body. Measured 2026-10-11 on
2026.0.315 with `Nardo`, 2019 mm tall including ears, whose tail reaches `z = -994`
behind: a 200 × 140 mm piece arranged on each point in turn, its 3D bounding box read
from an `ExportOBJ` (mm).

| Point | Where the piece landed | For comparison |
|---|---|---|
| `Body_Back_Center_1`/`2`/`3` | z -1126..-1176 | back surface z ≈ -116 |
| `Body_Back_1_L`/`_R` | z -799..-992 | |
| `Body_Front_Center_1`/`2` | z 386..436 | chest surface z ≈ +146 |
| `Body_Front_1_L` | x 155..207, z 57..250 | |
| `Neck_Back` | y 1535–1676, z -114..-179 | |
| `Neck_Collar` | z -157..-216 | |
| `Neck_Front` | y 1426–1556, z 93..173 | |
| `Neck_L` | x 131..161 | intersects the shoulder |
| `Shoulder_L` | x 101..289, y 1469–1517, z -78..62 | lies over the top of the shoulder |
| `Head` | z -524..-549 | |

The back-centre points are a metre behind the back and the front-centre points 240 mm
proud of the chest, while the neck and shoulder points sit close to, or in, the body.
**Never
infer an arrangement point's position from its name** — resolve the point's world
position first, and after arranging, read where the piece actually landed.

### Nothing reports an avatar's size

**Nothing found in the six modules reports an avatar's dimensions or a piece's 3D
bounds.** `utility_api.GetClothPositions(List[float]) -> None` looks like the reader and
cannot be one: its C++ side fills a `std::vector<float>&`, pybind11 passes it a copy of
the list, and the list handed in is never filled in (2026-10-11, 2026.0.315).

The route that works is **`ExportOBJ` with its option object, then parsing the file**.
`bExportAvatar = True`, `bExportGarment = False` wrote the avatar alone — 13,399 vertices
in 0.03 s — in millimetres, `y` up, `+z` the avatar's front; `Nardo`'s group was named
`Mesh`. Check the file exists before parsing it: see Execution environment for an export
that reports success and writes nothing.

**`+x` is the avatar's left.** A front piece arranged facing out had its 2D `+x` at 3D
`+x`, the avatar's left; a back piece arranged behind came back mirrored, 2D `+x` to the
avatar's right (2026-10-12). Which is why two identically drafted panels sewn side to
side take `ends="opposite"`.

**Sections, not vertex bands.** Genesis 8's torso vertices are sparse enough that a
16 mm band at 1150 mm split the torso into three clusters. Cutting the triangles with
the plane and joining the segments through the edges they cross gives one closed loop
per body part at every height, which is how `md_body` measures (waist 662 mm at 1157,
hip 1017 at 892, bust 937 at 1342, neck base 477 at 1507, measured as tape would: round
the convex hull).

### A garment vertex knows its pattern position

**Every garment vertex in an OBJ carries a `vt`, and it is the vertex's 2D pattern
position — up to an affine map that depends on the fabric.** Measured 2026-10-12 on
2026.0.315, one `g` group per piece and one `vt` per `v`:

| Fabric | `vt` of a 620 × 580 mm panel |
| --- | --- |
| MD's default `FABRIC 1` | millimetres, centred on the piece's 2D box: the corner drafted at (−310, 0) came back as `vt −310 −290` |
| `V2_Cut_Sew_Knit_Jersey_1` | about −2..2: scaled by the texture's size |
| `V2_Woven_Twill_1` | scaled differently along u and v |

So nothing should read `vt` as millimetres. `md_fit` registers each piece: it takes the
outline from `ExportPatternJSON`, normalises both outlines by their area moments (which
reduces any affine map to a rotation or reflection), searches the start and direction,
and then fits a full affine map to every outline vertex. On both fabrics above the worst
vertex landed within 0.12 mm of the pattern. Any trapezoid is its own mirror image under
an affine map, so for such pieces the map is chosen closest to the texture's own axes —
which is how MD lays its textures.

---

## Placing pieces in 3D

Drawn together from measurements elsewhere in this document, all on 2026.0.315.

**What moves a piece:** the arrangement point named in `SetArrangement`, which receives
the centre of the piece's 2D bounding box with 2D `+y` up, and
`SetArrangementOrientation`, in degrees from a default of 180, and
`SetArrangementPosition`'s position round and along the point's bounding volume and
distance from it — applied only when the piece is re-placed, by re-setting its
orientation (see Arrangement). The rest follows from how the piece is drafted: it extends
from that centre as far as its outline does.

**What does not:** the `fOffSetX`/`fOffSetY` in pattern JSON's `ArrangementPointDataMap`,
which import discards; and `ResetClothArrangement` after such an import.

**And a point's name does not say where it is.** Resolve the point's world position, and
after arranging export an OBJ and read the piece's bounding box — nothing else reports it.

**`Curved` can start a seam crossed.** A two-panel skirt on Genesis 8, arranged `Curved`
at `Body_Front_Center_5` and `Body_Back_Center_5` with the points' own values, wrapped
each panel round the hips: 513 of the front's vertices started inside the body, up to
47 mm deep, and the side edges ended past each other. Draped 600 steps, one side seam
stayed open over its lowest 100 mm while the ledger showed it sewn full length. The same
panels arranged `Flat` started as two planes, 290–315 mm apart at the seams, and draped
with both side seams closed (2026-10-12). `md_fit` before a drape reports the first case
— cloth inside the body — and `md_place` raises the offset until there is none.

**A path between seam partners that crosses a limb is usually fine.** T-shirt side seams
whose straight paths between partners crossed an arm and a sleeve in 4 of 16 places
closed in the drape, on the A-posed Genesis 8 (2026-10-12).

**Two arranged pieces through each other end up apart.** Jacket fronts with 40 mm
centre-front extensions, arranged at `Body_Front_3_L` and `_R`, started 84 mesh edges
through each other down the whole front; draped, one was flung round to its side seam,
140-160 mm off the body. `md_fit` reports pieces through each other, and `md_place`
raises a piece's offset until it is clear of every piece it placed before: the fronts
then started layered and stayed on the front (2026-10-12).

**Place sleeves before the body.** Placed after the back, `md_place` moved each sleeve
out to clear it (offset 68 to 81-90), and its underarm seam then closed through the
upper arm, where the arm joins the body and there is no end to slip round. Placed first,
at offset 67-71, the same sleeves draped clean and the back stepped out instead.

## Fitting tops on an A-posed avatar

Measured 2026-10-12 on Genesis 8 Female, 2026.0.315, with `md_fit`.

**Front and back are not quarters.** Genesis 8's bust is 560 mm round the front and 378
behind (`md_body`'s arcs, split at the widest points in x); the hips 481 and 536. A
jacket drafted in quarters had a back 160 mm too wide and fronts that could not cover
the bust; drafted to the arcs it fitted. A T-shirt likewise lost a fold at the sleeve.

**An armhole needs ease over the arm root.** Measured in the armhole's own plane the arm
root was 290-430 mm. Drafted at 430 mm the jacket's armholes stretched 12 % and the arm
came through the sleeve; at 477-495 mm, 1-6 %. Deepening it on an open front made
things worse, not better, until the front was held (below).

**A sleeve's underarm seam is the arm's length; the cap sits on top.** Holding a sleeve's
whole length fixed shortened its underarm seam as the cap grew taller, and the taut seam
cut into the arm's underside, 20-45 mm from it. A 400 mm seam with the cap above draped
clean; 420 put the cuff over the hand.

**Open fronts swing.** Unheld jacket fronts hung 250-480 mm apart and rotated the body
cloth round, carrying the side seams under the arms (x = 250-284 mm, against a 135 mm
torso) and the arms through the cloth. Splitting each centre-front edge round a 30 mm
tack at the waist and sewing the two tacks together held them: lapels 0-105 mm apart,
the skirt below 17-80 mm, the drape clean.

**A ring is measured against the body in its own plane.** A cuff's horizontal section
at its mean height cut the torso and both arms (817 mm); in the cuff's plane, the wrist
(167 mm). A neckline dropping 70 mm at the front went round 580-630 mm of body in its
plane against 477 mm horizontally at the neck base.

**Necklines carry an open jacket, and jersey stretches under its own weight.** With no
collar or facing a tweed jacket's neckline was 10-18 % longer than drafted however wide
or deep it was cut. Jersey T-shirt armholes with about 100 mm of ease settled 5-8 %
longer, and the hem 2 %; woven hems 0-1 %. `md_fit` warns above 5 %, so read a knit's
warnings against its fabric.

**Plain waists slide.** Shorts with 16 mm of waist ease slid 74 mm onto the hips and
stretched 17 %, taking the hems 60 mm down; elastic on the four waist lines held them at
1125 mm, 2.7 % stretched -- as on the skirt under Fabric and materials.

**A workaround that got cloth onto the body, and is not a way to build a garment:** draft
pieces short, so that their bounding-box centre, and so the piece, arranges near the
target; sew and simulate; then lengthen with `MovePatternPoint` between simulation runs.
In a real cape session (2026-10-11) the result was unusable: it re-meshes at every step,
drifts curves, and needed freezing and gravity toggling to work at all. Mechanics, if it
is used for a probe: it is an absolute move, so read each hem point's current position, add
the extra length, and send the sum. Read the outline back after each move, because one MD
will not accept leaves the piece unchanged without an error, and remember that a moved
corner carries the adjacent curve with it.

---

## Fabric and materials

**A garment hangs where its fabric stretches enough to hold it.** A two-panel skirt on
Genesis 8 with a 682 mm waist settled with its waist at 1069 mm, 827 mm long — **21 %
longer than drafted** — in MD's default `FABRIC 1`, and at 1099 mm, 13 % longer, in
`V2_Woven_Twill_1`. Drafting the waist 20 mm under the body's girth barely moved it
(1112 mm, 16 %), and 1500 further steps changed nothing: it is the drape's equilibrium,
not an unfinished one. The hem in the same drapes measured within 0.5 mm of its drafted
length. Measured 2026-10-12, 2026.0.315, with `md_fit`'s rings (`stretch_pct`). A
skirt that stays at the waist needs something the drape does not give a plain panel.

**Elastic on a line gives it.** `SetPatternPieceElastic(piece, line, True)` and
`SetPatternPieceElasticTotalLength(piece, line, mm)` on both waist edges of the twill
skirt above held its waist at 1149 mm — the body's waist is at 1157 — with the ring
1.3 % over its drafted length instead of 13 %. Total lengths of 300 and 250 mm per edge
gave the same drape: the ring tightened until the body stopped it. Neither call has a
getter; this is how to know they took effect.

**`fabric_api.GetFabricCount()` is unreliable rather than always wrong.** A new project
reports `0` while `GetFabricName(0)` answers `'FABRIC 1'` and the Object Browser shows
it — the same shape of bug as `GetPatternSize`. But a scene with four explicitly added
fabrics answered `4` (measured 2026-09-19, 2026.0.315), so code that treats `0` as the
tell will not recognise the working case. Use **`GetFabricStyleNameList()`** either way:
its length is the real count and its entries are the names.

**`GetFabricName(n)` past the end answers `''` instead of raising**, so it cannot be used
to probe for fabrics the count is hiding: indices 4 through 11 on a four-fabric scene all
returned an empty name and a `(0, 0, 0, 1)` colour rather than an error (2026-09-19,
2026.0.315). A silent empty string reads as "a fabric with no name", which is how a scene
grows imaginary materials in a report. `GetFabricStyleNameList()` is still the count, and
on that scene it agreed with the Object Browser exactly — four entries, same order.

**`AddFabric(_path)` takes a file path to a `.zfab`, not a name.** Handed a plain string
it adds nothing, raises nothing, and **returns `0`** — indistinguishable from having
successfully added the fabric at index 0. Check `GetFabricStyleNameList()` afterwards.

**Loading a preset is the only way to change how a fabric behaves.** No call anywhere in
the API sets a physical property — stretch, bending, density. `GetFabricInformation`
returns metadata only: `Classification`, `Color`, `ColorCode`, `Content`, `Owner`,
`Print`, `SupplierName`, `Texture`, `Thickness` (`'0.5 mm'`) and `Weight`
(`'300 g/m2'`), as strings. `GetFabricInfo` returned only
`{"rollWidth": 44, "styleChipList": []}` (both 2026-10-11, 2026.0.315). So choose the
behaviour by handing `AddFabric` one of the shipped `.zfab` presets:

- `C:/Users/Public/Documents/MarvelousDesigner/New Assets/Fabric/*.zfab` — about 70:
  `V2_Woven_*`, `V2_Cut_Sew_Knit_*`, `V2_Animal_Alternatives_*`, `V2_Leather_Full_Grain`,
  `V2_Non-Fabric_*` and `(Default for Simulation)`, plus two more under `Elastic/`.
- `<MD install>/Preset/Bond/*.zfab` — fusibles and reinforcements.

`SetFabricName` and `SetFabricPBRMaterialBaseColor(_fabric, _colorway, r, g, b, a)`
work and read back.

**`pattern_api.SetPatternPieceFabricIndex` reads back without necessarily rebinding what
the piece renders as.** Measured 2026-09-19 on 2026.0.315, on a garment whose trims had
been merged in from a separate `.zpac`: twelve pieces reported fabric `1`, and recolouring
fabric `1` to red repainted some of them and left the rest their original colour. Setting
those pieces to fabric `0` or `3` changed the reported index and nothing on screen.
`fabric_api.GetFabricIndexForPattern` agreed with the wrong answer, and
`fabric_api.AssignFabricToPattern` returned `False` in both argument orders. There was no
second material to find — `GetFabricStyleNameList()` had four entries and no fabric
carried a texture — so the binding appears to dangle and fall back to MD's default tan.

The readback agreeing is therefore not evidence. **Recolour the fabric and look**: pieces
that follow the change are the ones really on it.

**Check it is a pattern piece before blaming the binding.** On a 56-piece garment
(2026-09-19), all four fabrics were set to saturated probe colours and then every one of
the 56 pattern pieces was set to a single fabric. Body, waistband, cuffs, zip welts and zip teeth all followed. A set of
strap-shaped ribbons did not, and held the same tan through every combination — so they
are not pieces bound to the wrong fabric, they are geometry that
`SetPatternPieceFabricIndex` does not reach at all. Also ruled out: trims
(`GetTrimStyleCount` answers `0`, and the Object Browser lists only the two defaults),
topstitching (recolouring the style those pieces sit on changed nothing), selection
highlight (`GetSelectedPattern()` empty), layer colour-coding (all 56 on layer 0), and the
avatar (its `.mtl` has two materials, both white).

**It was the topstitching.** Settled by exporting the scene and reading the groups:
188 groups named `Topstitch_*`, all carrying one material (`Material4597`, white with a
stitch texture), against 56 groups for the pattern pieces — exactly matching the 188
assignments `GetPatternAssignedTopstitchCount` reports across the garment. Topstitch
geometry is generated by MD, is not a pattern piece, and is reached by none of the fabric
calls; at a large enough width it stops reading as stitching and starts reading as tan
webbing lying on the garment. Two sessions spent recolouring fabrics were recolouring the
wrong object.

The transferable part is the order: prove the surface is a pattern piece before reasoning
about which fabric it points at — and **`ExportOBJ` with its option object is the fastest
way to find out**, because the exported group names say what every surface actually is.

**`AssignFabricToPattern` takes three ints, not two** — `(arg0: int, arg1: int, arg2: int)
-> bool` in the live build. An attempt written against a two-argument shape is an arity
mistake, which MD reports as a signature error rather than as a refusal to bind.

**`SetFabricPBRMaterialBaseColor` with a side index of `1` ended the MD process**
(2026.0.315, 2026-09-19). Reads at that index are fine and return the back-side colour;
the write took the host down with no dialog and no traceback, losing the unsaved scene.
Side `0` is safe. Save before touching the second side, if you touch it at all.

---

## Trims and finishing

**`utility_api.GetTrimStyleCount()` reports `0` for a scene that has trims.** Both
`GetTrimStyleCount(True)` and `(False)` answered `0` on a scene whose Object Browser
listed `Default Button` and `Default Buttonhole` (2026-09-19, 2026.0.315). It joins
`GetFabricCount` and `GetPatternSize` as a counter that cannot be trusted as the tell.

**`pattern_api.ExportObjectBrowserMaterialsList()` is the readable inventory**, and
despite the name it exports nothing and takes no path — it returns a short string listing
the scene's fabrics and trims under headings, numbered from 1:

```
Fabrics
1:Sage olive cotton twill
...
Trims
1:Default Button
2:Default Buttonhole
```

Around 160 characters for a four-fabric scene, against ~99KB for a full `md_api` dump.
When a count and the Object Browser disagree, this is the one that matched what MD drew.

**Topstitching works.** `GetTopstitchStyleList()` answers
`[{'TopstitchStyleIndex': '0', 'TopstitchStyleName': 'Default Topstitch'}]`,
`AddSegmentTopstitch(_pattern, _line, _style)` returns `True`, and
`GetPatternAssignedTopstitchCount(_pattern)` then reports `1`. It is one of the few
additions with a countable readback.

**Elastic, shirring and seamtaping are accepted** — `SetPatternPieceElastic`,
`SetPatternPieceElasticStrength`, `SetPatternPieceShirring` and
`SetPatternPieceSeamtaping` all return `None` without complaint. There is **no getter for
any of them**, so "accepted" is the whole of what can be said; nothing here claims they
took effect.

**Not exercised at all:** the `SetZipper*Style` family, `GenerateZippersFromObj`,
`SetButtonHeadStyleColor` / `SetButtonHoleStyleColor`. Present in the build is not the
same as known to work. Use `md_api` and try them.

**On the elastic, shirring and seamtaping setters, `_lineIndex == -1` means "every edge
of the piece".** An off-by-one that produces `-1` silently does the whole piece.

---

## Reading a scene back

**Both information calls hand back a JSON string, not a dict.** Measured 2026-09-19 on
2026.0.315: `GetPatternInformation(0)` and `GetPatternInputInformation(0)` both return
`str`, so `.keys()` raises `AttributeError` and `[...]` raises `TypeError: string
indices must be integers` — which reads as the call being broken rather than as needing
`json.loads`. Parsed, the input information is
`{"Pattern InputInformation": [ {...} ]}` — **the key contains a space** — holding one
record per call with `Pattern index`, `Pattern name`, `LineList` and `PointList`.

**`PointList[0]` is a header, not a point**, and so is `LineList[0]`: they are
`{"Point count": "8"}` and `{"Line count": "8"}`, with the data following at 1..n.
Iterating either straight raises `KeyError: 'Point positionX'` on the first element. A
line's `Line type` is `"Straight type"` for a straight line and `""` for a curved one.

`GetPatternInformation` returns id, name, uuid, thumbnail, `isTrimItem` and
`isSeamlessBlock` — **no geometry**, despite its documentation.
`GetPatternInputInformation` is the one with per-line and per-point data, including line
lengths and types.

Its `PointList` holds **only the straight points**: a 7-point contour with 3 spline
points reports 4. A sampled curve's interior is in neither information call;
**`ExportPatternJSON` carries it**: the document
lists every point of every line, a type-`2` point coming back as `PointType "Polyline"`
(2026-10-11, 2026.0.315). A shape can still be wrong in ways nothing but simulation or an
eye will show.

**A piece's 2D box is two calls.** `GetBoundingBoxOfPattern(i)` answers only
`{"Width": "300.000000", "Height": "300.000000"}` — strings, and no position — and
`GetPatternPiecePos(i)` the box's centre (2026-10-12, 2026.0.315).

### Keeping data with the garment

**`utility_api.SetAPIMetaData(key, value)` returned `False` for every key tried and
stored nothing**; `GetAPIMetaData` then answered `''`. Measured 2026-10-12 on 2026.0.315,
on an empty scene and on one holding a piece, with a dotted key and a plain one.

**The garment's own metadata works.** `GetMetaDataForCurrentGarment()` returns a JSON
object as a string, `"{\n}"` for a new project. `ChangeMetaDataValueForCurrentGarment(key,
value)` adds or replaces one key and leaves the others — a second key was added beside
the first — while `SetMetaDataForCurrentGarment(json)` replaces the whole object. Same
day, same build:

| After | The metadata |
| --- | --- |
| `ExportZPac`, `NewProject`, `ImportZpac` of that file | back, both keys intact |
| `NewProject` | `"{\n}"` |

So it travels with a checkpoint. `md_draft_piece` keeps its edge names under the key
`md_mcp.pieces`, and `md_restore` brings them back with the garment.

---

## Adding a sewing pattern

Two routes, and they reach different things.

**Call by call** — `CreatePatternWithPoints` for the outline, `AddSeamlinePairGroup` for
the seams. Everything above about line indices applies, and darts and pleats have to be
drawn into the contour, which is what a flat-pattern drafter does anyway.

**A `.zprj` survives a round trip intact.** Measured 2026-09-19 on 2026.0.315: a
56-piece garment exported with `ExportZPrj` and read straight back with
`ImportZprj(path, ApiTypes.ImportZPRJOption())` returned identical piece count and
names, identical per-piece fabric indices and simulation layers, an identical seam-group
count (133) and an identical `GetFabricStyleNameList()`. The reimport replaces the open
scene rather than adding to it, and took ~2s. Nothing measurable was lost.

**But saving a `.zprj` hijacks MD's open project.** Measured 2026-10-11 on 2026.0.315,
with `utility_api.GetProjectFilePath()` (which answers `"NULL"` for a project never
saved), `GetProjectName()` and `CheckZPRJForUnsavedChanges()` read after each call:

| Call | Project path afterwards | Unsaved flag afterwards |
| --- | --- | --- |
| `ExportZPrj(path)`, `ExportZPrj(path, False)`, `ExportZPrj(path, True)` | the copy | cleared |
| `ExportZPac(path)` | unchanged — a saved project kept its path, an unsaved one stayed `NULL` | cleared (the name changes for an unsaved project) |
| `ImportZpac(path, ImportExportOption())` | unchanged | cleared |

So after an `ExportZPrj` "copy", the user's next Save writes over the copy and not their
project; there is no setter to put the path back. **A `.zpac` is the garment only** —
after `NewProject` an imported one brought back pieces and seams and no avatar — and
**`ImportZpac` replaces the garment and keeps the open avatar**: imported onto a scene
holding an extra piece and Genesis 8, the extra piece went and the avatar stayed. That
makes `.zpac` the checkpoint format (`md_checkpoint`).

**Every save of a copy clears the unsaved flag**, so MD would close without asking
although the user's own file lacks their latest work. Renaming a piece does not set the
flag again, even to a different name and back (an earlier reading that it did was a
coincidence). Creating a piece sets it, and deleting that piece afterwards leaves it set
with the garment exactly as it was — which is what `md_checkpoint` and `md_restore` do.

**As a document** — `pattern_api.ExportPatternJSON(_path)` / `ImportPatternJSON(_path)`.
Measured round trip: two sewn panels exported, `NewProject`, re-imported — both pieces
back with their names, both seam groups back, and a 360 mm edge still exactly 360 mm.
Import **replaces** the scene — but not all of it. Measured 2026-10-11 on 2026.0.315, the
avatar stayed, and the document's fabric was added beside the one already there:
`GetFabricStyleNameList()` went from `['FABRIC 1']` to `['FABRIC 1', 'FABRIC 1']`. Check
the fabric list after an import.

The document is richer than the call API, which is the reason to know about it:

```
formatVersion, Unit ("mm"), generator
FabricList              FabricName, FabricUUID, FabricType, strBaseColorHexCode, …
PatternList[]           ID, Name, CurrentFabricUUID, fGrainlineAngle, IsClosed,
                        IsHalfSymmetric, ShapeInfo.LineList[].PointList[]
                          (ID, PointType "Straight"/"Polyline"/…, GradingRuleID,
                          Position{x,y}),
                        InternalLineList, NotchList, OuterSeamAllowanceList,
                        ButtonHeadList, ButtonHoleList, AnnotationList,
                        ArrangementPointDataMap
                          {PointName, fOffSetX, fOffSetY, fAngle}
SeamLinePairGroupList[] Name, bIsTurned, FoldData{iAngle,iStrength}, PairList[]
                          {First,Second}: ShapeID, LengthParam{fStart,fEnd}, Direction
GradingRuleTableList, SymmetricDataList, InstanceDataList, POMList
```

**Notches, seam allowances and button placement have no setter anywhere in the six
modules, but the document has a field for each** — so authoring JSON is the only way to
reach them.

**A seam here is an arc-length span, not a line index**: `fStart`/`fEnd` are fractions
along the outline of the piece named by `ShapeID`. The entire line-index failure class
cannot be expressed in this format.

**MD validates none of it.** An authored notch with invented field names was accepted,
the omitted fields read from uninitialised memory (`fWidth` = 6.6e-43), and no error was
raised. It reads documents you author, not only ones MD exported — `PatternList` alone
suffices and your IDs survive — but write every field explicitly. The safest way is to
export a document from a scene MD built and edit that, rather than authoring one from
nothing.

**The arrangement offsets in the document are discarded.** With a piece's
`ArrangementPointDataMap.fOffSetY` set to `-300` and the document imported, the piece's
3D bounding box was identical to the millimetre, a fresh export read `fOffSetY` back as
`0.0`, and `utility_api.ResetClothArrangement()` afterwards did not move it either
(2026-10-11, 2026.0.315). The working per-piece offset is `SetArrangementPosition`
followed by a re-placement — see Arrangement.

**DXF is not reachable.** `ApiTypes.ImportDxfOption` and `ExportDxfOption` both exist,
with the full set of grading, notch and seam-allowance fields, and **no function in the
build takes either of them** — there is no `ImportDXF`/`ExportDXF` call, and no signature
anywhere mentions DXF. The structs are vestigial. `.zprj`, `.zpac` and pattern JSON are
the formats that actually move a pattern in and out.

---

## API quirks

**Calls that report success and do nothing** (all measured 2026-09-19 on 2026.0.315):

| Call | What it does |
| --- | --- |
| `utility_api.SetViewPoint(0..5)` | Returns `None`; the camera does not move. **`SetCamViewPoint` is the one that works** — see below. |
| `utility_api.GetViewPoint()` | Answers `-1` whatever has been set, before and after either setter. |
| `export_api.ExportTurntableImages(path, n, w, h, 0)` | Returns `[]` and writes no files. |
| `export_api.ExportCustomViewSnapshot(dir, w, h, prefix)` | Returns `[]` and writes no files. |
| `fabric_api.AssignFabricToPattern(a, b, 0)` | Returns `False` for both argument orders. |

`export_api.ExportSnapshot3D(path)` **does** work, and is the one that can be relied on —
note it returns a list of lists, `[['C:/.../shot.png']]`.

With MD's 3D window closed it raises a modal "Snapshot is only available when the 3D
window is displayed" instead, which holds the main thread until clicked (2026-10-12).

**`export_api.ExportSnapshot2D(path, mode)` draws the 2D pattern**, and returns `[]`
having written the file. Mode 0 draws every piece's outline whole, about 10500 px wide,
whatever the 2D window shows; mode 1 captured the 2D window as it was zoomed. On an empty
board it raises a modal "There are no patterns". Measured 2026-10-12, 2026.0.315: mode 0
between two 3D snapshots left the 3D window open. The window had been found closed after
the first trial of modes 0-2, and mode 1 was not called again to find out which closed it.

### Seeing the garment: `SetCamViewPoint`, not `SetViewPoint`

**`utility_api.SetCamViewPoint(n)` moves the 3D camera. `utility_api.SetViewPoint(n)`
does not.** The names are one word apart and only one of them does anything, which is
expensive to discover: without the right one there is no way to look at the back of a
garment, and the obvious-looking call fails silently.

`SetCamViewPoint` takes 0 to 9, exactly as MD's own API list documents them — measured by
eye on 2026-10-11, 2026.0.315, with `Nardo`:

| n | View |
| --- | --- |
| 0 | bottom |
| 1 | three-quarter right |
| 2 | front |
| 3 | three-quarter left |
| 4 | right |
| 5 | top |
| 6 | left |
| 7 | focus zoom: keeps the direction — after 2 it zoomed in on the front, and after 6 its snapshot was byte-identical to 6's |
| 8 | **back** |
| 9 | zoom to fit: keeps the direction — after 2 the whole front, after 4 the whole side, after 8 the whole back |

So the back is 8, outside the 0..7 this document claimed from 2026-09-19 as "eight
distinct views … round the back" — 7 only repeated 6 there. Follow it with
`utility_api.Refresh3DWindow()` and then `export_api.ExportSnapshot3D(path)`.

**Snapshot bytes move once there is cloth, and hold still before that.** Sixteen
captures of one draped garment at one camera position produced sixteen different
SHA-256s, and repeated `Refresh3DWindow` calls do not converge. But the instability is
not unconditional: measured 2026-09-19 on 2026.0.315, three captures of an empty scene
were byte-identical, three of two sewn 360 × 500 mm panels *before* simulating were
byte-identical, and the same three after `Simulate(400)` were not. That was the old,
never-returning listener. Under the timer listener (2026-10-11) the flat captures were
still identical, and the draped ones were identical in two runs out of three and
differed in the third.

So a hash is evidence in neither direction — it reports "changed" for a draped scene
nobody touched, and "unchanged" is only ever as good as how little is in the scene.
**Compare the pictures, not their hashes** — which for two `SetViewPoint` values that
hashed differently turned out to be the same view.

---

**`GetPatternPieceSolidifyStrengthen` answered `534070.75` for every piece in a 58-piece
scene**, base panels and trims alike. The getter is not broken — setting `0.5` reads back
`0.5`, and writing the original value back restores it — so that number is the *default*
a piece carries when solidify has never been set on it. Read it as "unset", not as a
strength, and do not infer from it that the getter is unreliable.

Round-tripped on 2026-09-19 (2026.0.315) and all six read back what was written, then
restored: `SetPatternPieceGrainDirection`, `SetPatternPieceCategory`,
`SetPatternPieceSolidifyStrengthen`, `SetParticleDistanceOfPattern`,
`SetAddlThicknessCollision`, `SetPatternLayer`.



**Overloads dispatch on arity and argument type.** Always pass positional arguments, and
never `None` as a placeholder — it selects a different overload. `AddSeamlinePairGroup`'s
three forms are segment↔segment (6 args), segment↔innershape (7) and
innershape↔innershape (8). 77 calls in this build are overloaded; `md_api` lists every
signature for each, because pybind11's own first docstring line for an overloaded
function is the useless `Foo(*args, **kwargs)`.

**37 calls take an MD option object** (`ImportExportOption`, `ImportZPRJOption` and
friends), which has no JSON form. `md_call` cannot reach those with any arguments; build
the object in `run_md_python` instead. `md_api` reports each struct's fields and
defaults, which MD documents nowhere else.

**Unicode variants.** Many calls have a `…W` twin (`ExportZPrjW`, `GetPatternIndexW`).
These are the wide-string forms; use them for file paths, since Windows user directories
routinely contain non-ASCII characters.

**Names that are typos in MD itself.** Reproduce them verbatim or the call will not
resolve: `SetPatternPieceSametapingWidth` (not `Seamtaping`),
`DistribueInternalLinesbetweenSegments` (not `Distribute`), `DeletePoint(_poinIndex)`.

**Calls that return nothing useful.** `SymmetryPatternPiece`, `InstancePatternPiece` and
`LayerClone*` return `None`, so there is no way to learn the index of the piece they
created. Anything that must be sewn afterwards has to be mirrored with
`CopyPatternPieceMove` (which returns an index) plus `FlipPatternPiece`.

**Write-only settings.** `pattern_api.SetPatternFreeze(_index, _bool)` has no getter
anywhere in the six modules, so a script cannot record the freeze state it is about to
change and cannot put it back. Combined with there being no undo, freeze is a one-way
edit: reload the project if the previous state mattered.

**Not exposed at all:** darts · pleats · bezier tangent handles · reading a piece's line
list · a fabric's physical properties (load a preset `.zfab`) · undo. Seam allowances,
notches and button placement are not exposed *as calls*, but are reachable through
pattern JSON.

---

## Execution environment

MD's interpreter is **CPython 3.11.8**, with `socket`, `threading`, `http.server`,
`subprocess` and `ssl` importable.

**Python threads only run while a script is running.** An MD API call from a background thread
does work — but only during a script. The moment the script returns, MD stops releasing
the GIL and every Python thread freezes where it stands: a thread ticked 20 times while
its script's main thread slept two seconds, and zero times afterwards, with `is_alive()`
still `True`. This corrects an earlier reading of the same measurement, which took "a
worker thread works" to mean a worker thread keeps working.

**The API never calls Python back** — there is no idle, timer or event callback in it —
**but Windows will.** A `user32.SetTimer` thread timer armed through `ctypes` is
dispatched by MD's own message loop on MD's main thread, after the script that armed it
has returned. Measured 2026-10-11 on 2026.0.315: a top-level Python Editor Run armed a
25 ms timer polling a non-blocking socket and returned at once; with nothing else
running, the callback answered 100 requests at a 31 ms median (one tick: Windows rounds
25 ms up), ran on the thread that armed it, and called `pattern_api.GetPatternCount()`
without error. MD's viewport and menus stayed responsive, and closing MD ended the
process — which the never-returning listener does not. The callback object must be
kept referenced (it was stored on `builtins`), and an exception must never leave it.

So a script that does not return is no longer the only way to outlive a script, and the
listener is no longer one: from 2026-10-11 **Start MCP** arms a 10 ms timer and returns,
and **Stop MCP** killed it; since 2026-10-12 one **Start/Stop MCP** entry does both (see
Registering a plugin). Before that the listener was a Run that never finished,
pumping MD's message queue to keep MD usable while it held the main thread, and closing
MD left the process running behind it.

**A tick must never run inside another.** An MD call that pumps the message queue — a
modal dialog does — lets the timer fire inside the request that made the call, and a
tick that served there would run a second request in the middle of the first. The
listener's tick returns at once when one is already running.

**A modal dialog raised by an MD call stops everything**, for the same reason: it holds
the main thread, and nothing at MD's end times out. See Sewing for the measured case.

**`export_api.ExportOBJ` must be given its option object.** Measured 2026-09-19 on
2026.0.315, same scene (56 pieces plus an avatar), same destination, minutes apart:

| Overload | Result |
| --- | --- |
| `ExportOBJ(path)` | No reply in **300s**, **zero bytes** written, a `ping` 5s in also unanswered. MD stayed alive and began answering again minutes later. |
| `ExportOBJ(path, ApiTypes.ImportExportOption())` | Returned in **4.7s** with the `.obj` and `.mtl` paths, 108MB written. |

So the single-string overload is the fault and the scene is not: pass an
`ImportExportOption` and the export is fast and complete. Whether the bare-path form
raises a modal options dialog was never confirmed — it produced no partial file, which
rules out "merely slow" — but the cause stopped mattering once the working form was
found. This is the same shape as the 37 other calls that take an MD option object: the
overload without one is not the convenient version, it is the one that stops.

**And the paths it returns are not evidence of a write.** Measured 2026-10-11 on
2026.0.315: `ExportOBJ(path, ImportExportOption)` into a folder that did not exist
returned the `[".obj", ".mtl"]` path list exactly as on success, and wrote nothing. After
`os.makedirs` on the folder, the identical call wrote both files. Create the folder first,
and check the files exist before reading anything from them.

Scripts run inside MD via **Main Menu → Plugins → Python Editor** — there is no Script
menu in this build, whatever the published docs say — or from a plugin registered through
Plugin tab → Plug-in Manager → + ADD. Only `.py` files, and no manifest.

### A timeout abandons the reply, not the work

Measured 2026-09-19: a script told to sleep six seconds, given up on after two, **ran to
completion**. The bridge answered normally 4.5s later and the scene was intact. Nothing
at MD's end is cancelled, so a build that times out has still been applied — retrying it
applies it twice. Read the scene back before deciding whether anything is left to do.

**A second request waits rather than failing.** The listener serves one connection at a
time on MD's main thread, so a `ping` sent while a 4s script was running answered after
3.4s, correctly and without desync. A short timeout can therefore fire because something
else is busy, not because anything is wrong.

### A stale listener produces tracebacks that cannot be read

The listener is a script MD loaded when **Start/Stop MCP** was clicked. Editing
`md_mcp_listener.py` afterwards does not touch the running instance — and because Python
reads the file off disk when it formats a traceback, a failure then reports **the running
instance's line numbers beside the new file's source**. The two disagree, so the
traceback points at unrelated code: a division-by-zero inside a script was reported at
`line 228, in _run` with a bare `)` as the offending line.

That reads as a bug in the listener. It is not; it means the listener in MD predates the
file. Failure replies carry `plugin_version` and `plugin_build` for this reason, and the
bridge appends them to the error. If the build is not the one just installed, **click
Start/Stop MCP twice** (stop, then start), which reads the file afresh — the scene lives in MD, not in
the listener, so restarting it loses nothing.

### Registering a plugin

**`utility_api.RegisterPythonScript(path, title)` takes the path first.** The binding
names its parameters `arg0, arg1`, and this document used to say `(_name, _path)`. MD's
own plugin log settles it:
`C:\Users\Public\Documents\MarvelousDesigner\New Assets\api_plugin_log.txt` records every
registration attempt and why it failed. Measured 2026-10-11 on 2026.0.315:

| Call | Log | Returned |
| --- | --- | --- |
| `RegisterPythonScriptW("Start MCP", <listener>)`, from a Plugin tab click | "Failed to register Python script because the script path is invalid: C:/Program Files/Marvelous Designer Enterprise Network Offline/Start MCP" | `False` |
| `RegisterPythonScriptW(<listener>, "Start MCP")`, over the bridge | "Skipped Python script registration because the script is already registered: …/md_mcp_listener.py" | `False` |
| `RegisterPythonScriptW(<md_mcp_stop.py>, "Stop MCP")`, over the bridge | "Skipped … already registered: …/md_mcp_stop.py", though it never had been | `False` |

So the title went in as a path relative to MD's install folder. The same log holds an
older line ending "…/md-mcp", from this repo's first registration attempts — which were
long read here as **MD refusing registrations made from inside another script**. That
theory explained four failures through the old bridge; argument order explains those and
these too, and nothing now supports the nesting theory. The entry that worked on
2026-09-19 is titled `md_mcp_listener`, after the file rather than any title this repo
passed, so it was very likely added another way.

**MD would not take a second script from the listener's folder.** On 2026-10-12, MD
started fresh with only the hand-added `md_mcp_listener` entry in
`Plugins/pluginSettings.json`, the listener's registration of `md_mcp_stop.py` was
logged as "Skipped Python script registration because the script is already registered"
and no Stop MCP entry appeared in the Plugin tab, then or in the saved list. Whether MD
keys registrations on the folder is not settled -- one folder, one refusal -- but the
consequence was: a bridge that could be started and not stopped. Since then the
listener registers one entry, **Start/Stop MCP**, and a click while it is listening
stops it.

**A path MD already has is declined, whatever the title**, and a new script in the same
folder as a registered one was declined too, with a log line claiming it was already
registered. Why is not known; a per-folder check is a guess. Check the bool and read the
log.

**The plugin list is a file:**
`C:\Users\Public\Documents\MarvelousDesigner\Plugins\pluginSettings.json` holds every
entry — `m_PlugInFileName`, `m_PlugInTitle`, `m_BaseMenuTreeByObjectName`
(`"Plugins / Plug-in"`), `m_SourceType` (`"script"`) — and MD rewrote it as it exited
(2026-10-11, at the moment MD closed). So "is it already registered?" can be answered by
reading that file, which this document used to say was impossible, and an entry could
probably be added or removed by editing it while MD is closed. That last part is
unmeasured.

`RegisterPythonScriptFolder` is **not exercised**, and its argument order is unmeasured
too — whether it produces a group rather than loose entries is worth an hour to whoever
needs a submenu. Both calls have `…W` twins.

**Nothing in the API unregisters.** There is no remover in the six modules, so from
Python an entry can only be removed by hand in Plug-in Manager — or, unmeasured, by
editing `pluginSettings.json` with MD closed.

**A plugin clicked while another script is running executes *nested* inside it, on MD's
main thread.** Measured through the bridge on 2026.0.315: with the serve loop holding the
main thread and pumping messages, a script run at that moment reported
`threading.current_thread() is threading.main_thread()` and could read the running
listener's own state off `builtins`. Two things follow. A second script can reach and
change the first one's state, which is how the old listener's Stop button worked — it
set a flag and returned, and the loop below it acted on the flag when it resumed. And the
first script cannot act on anything until the second returns, which is why a clicked
script has to return promptly and why a modal dialog in it stops everything. With the
timer listener nothing is running between requests, so a second click on Start/Stop MCP
runs at top level and finds the listener on `builtins` all the same.

**What MD gives a clicked plugin is not known.** Whether `__file__` or `__name__` is set,
and where its `print` output goes, have not been measured. Anything registered should
depend on none of the three: write the paths in, call what you mean explicitly, and
report through `DisplayMessageBox`.

### UI from a plugin

`utility_api.DisplayMessageBox(_message)` exists and is modal — see the note above about
what a modal dialog does to the main thread. `CreateProgressBar` / `SetProgress` /
`DeleteProgressBar` are present and unexercised.

### MD's own UI, from Python

**MD is Qt 6.10.3 Widgets**, and it exposes three hooks for plugin UI:
`RegisterWidget(_pointer : int)`, `UpdateCloStyleForPlugIn(_widget : QWidget)` and
`GetStyleSheetCodeForWidget(_type : CloWidgetType)`, with `DeleteWidgets()` and
`ResetWidgetRegistry()` beside them. **MD ships no Qt binding**, so out of the box none
of them can be reached: `PySide6`, `PySide2`, `shiboken6`, `shiboken2`, `PyQt6`, `PyQt5`
and `sip` every one raises `ModuleNotFoundError`.

**Installing PySide6 into MD's interpreter works, and it binds to MD's own Qt.**
`sys.path` includes
`C:/Users/Public/Documents/MarvelousDesigner/Configuration/python311/Lib/site-packages`,
which is user-writable. With `PySide6-Essentials==6.10.3` — the same version as MD's
`Qt6Core.dll` — dropped in, `QApplication.instance()` inside MD returns **MD's own
application object**, not a second one. Windows resolves PySide6's Qt DLL imports to the
copies MD already has loaded, so there is one Qt in the process and widgets can be
shared. Measured on 2026.0.315, 2026-09-19; a *mismatched* build would be a crash rather
than an ImportError, so match `Qt6Core.dll`'s file version exactly.

What then works, and what still does not:

| | |
|---|---|
| A `QWidget` made in MD's application | **Works.** It inherits MD's palette (`#1e1e1e` window, `#2d2d2d` base, `#3c3c3c` button, `#0078d4` highlight) and MD's font (Inter 9pt) with nothing set on it, so it looks like MD because it is MD's Qt. Parent it to MD's main window — the largest *visible* top-level widget, a `QFrame`, among some 470 hidden ones. |
| `UpdateCloStyleForPlugIn(widget)` | **Unreachable.** Refuses a shiboken widget: `TypeError: incompatible function arguments … 1. (arg0: QWidget)`. MD's pybind11 binding has no caster for PySide6 objects. Nothing is lost — a widget in MD's application is already styled. |
| `GetStyleSheetCodeForWidget(type)` | **Uncallable at all.** It wants a `Marvelous::CloWidgetType`, and that enum is exposed nowhere in `ApiTypes`; an int is refused. An API with no way to build its argument. (MD's app-level stylesheet is one line, `QToolTip { font-size: 12px; }` — the look is in the palette, not a sheet.) |
| `RegisterWidget(pointer)` | **Accepted and does nothing visible.** Given `shiboken6.getCppPointer(widget)[0]` it returns without complaint and MD survives, but the widget is not reparented, docked or shown. With `DeleteWidgets` and `ResetWidgetRegistry` beside it, it reads as ownership bookkeeping, not a way into MD's layout. |

**So a plugin can have a Qt window, and cannot dock one.** A tool window parented to
MD's main window is as far in as Python gets — and it is worth saying that the result
still does not read as MD: stock Qt widgets wearing MD's palette look like a dark Qt
dialog, not like MD's own controls, which are drawn by MD. `plugin/md_mcp_listener.py`
tried this, kept the measurements, and went back to a plain `user32` window rather than
put ninety megabytes of PySide6 inside MD's interpreter for it — and since 2026-10-11 has
no window at all, only Start/Stop MCP in the Plugin tab.

**A Qt widget must be driven from MD's main thread, without a loop of its own.** The old
listener refreshed its panel from its serve loop, which pumped the Win32 queue and called
`QApplication.processEvents()`; a timer callback would do the same job now. Never
`exec()`/`mainloop()`: that would hold the thread for as long as the window was open.

**Qt does not accept posted synthetic clicks.** `PostMessage(WM_LBUTTONDOWN)` to the
panel's HWND did nothing; `SendInput` against a raised, foregrounded window worked, and
so does a person. Worth knowing before concluding a button is broken.

**Tk is shipped without Tcl, so `tkinter` imports and cannot open a window.**
`import tkinter` succeeds, `_tkinter.pyd`, `tcl86t.dll` and `tk86t.dll` are all in
`…/Configuration/python311/DLLs`, and `Tk()` then raises `TclError: Can't find a usable
init.tcl` — MD ships no Tcl script library at all, and there is no `init.tcl` anywhere
under its install or its configuration directory (measured on 2026.0.315, 2026-09-19).
An importable `tkinter` is therefore not a usable one; borrowing another Python's Tcl
would work only while that Python stays installed and its Tcl stays 8.6.

**A plugin can put up its own window, built straight on `user32` through `ctypes`** —
measured inside MD 2026.0.315 on 2026-09-19, not merely written. `RegisterClassW` and
`CreateWindowExW` succeed from a script, the window appears over MD, and the serve
loop's existing `PeekMessageW` pump dispatches its messages: `WM_COMMAND` posted to it
from *another process* ran its window procedure on MD's main thread and stopped and
restarted the bridge, with the labels following each change. No toolkit and nothing
installed. (The listener dropped its window on 2026-10-11; this stays as a measurement.)

Two rules come with it. **The window must never run a loop of its own** — it is
refreshed and pumped by whatever script is holding the main thread, and a `mainloop()`
or a modal dialog would hold that thread for as long as it was open. And **lay it out in
96-dpi units and scale**: MD's window reported 120 dpi here, and a layout in raw pixels
loses the right-hand end of its own text.

**There is no `REST_API` module in this build**; importing it raises
`ModuleNotFoundError`. The six modules that exist are `pattern_api` (147 members),
`utility_api` (278), `export_api` (37), `import_api` (22), `fabric_api` (112) and
`ApiTypes` (92) — 688 in total.

`utility_api.GetMajorVersion()` / `GetMinorVersion()` / `GetPatchVersion()` report the
build. The published docs are "Marvelous Designer API 0.1", covering 2024.2.189 through
2025.1.201.
