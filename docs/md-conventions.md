# How Marvelous Designer actually behaves

Measured against **MD 2026.0.315** (Enterprise Network Offline) unless a line says
otherwise. Everything here was learned by building scenes in MD and reading them back.
None of it is guesswork, and none of it is a plan.

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
piece within it.

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

MD interpolates close to the true arc, so a sampled curve always measures **long**
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
points is the floor for a curve that has to match a seam; below that the spline does not
recover the intended shape either.

A chord-length-parameterized natural cubic spline reproduces MD's recorded lengths to
better than 0.01 mm across these densities, if you need to predict a length before
building.

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
closing back to point `0`. Two consequences:

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

### Seams can be read back per piece, not just counted

`GetSeamlinePairGroupListInPattern(_pattern)` returns the seam group ids that touch one
piece. That is more than `GetSeamlinePairGroupCount()` gives, and it is the closest thing
to checking a seam rather than trusting it:

* **A piece with an empty list is sewn to nothing.** On a finished garment that is a
  defect the count alone cannot show — the total goes up whether or not the piece it was
  meant for was involved.
* **Mirrored pieces should have equal counts.** Comparing every `*_L` against its `*_R`
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
directions, so the pair is `(True, False)`.

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
`GetParticleDistanceOfPattern` — the one per-piece physics setting that can be proven
rather than merely accepted. `SetPatternFreeze` and `SetAddlThicknessCollision` are
accepted and have no readback.

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

**`SetArrangementPosition` does nothing.** Swept over five values with an avatar loaded,
every exported vertex was identical, while `SetArrangementOrientation` on the same piece
moved it 342 mm. The readback echoes the integer back, which is why it looks as though it
worked.

`SetArrangementOrientation`'s `_orientation` is **degrees**, and the default is 180, not
0. `SetArrangementShapeStyle` accepts only `"Flat"` and `"Curved"`.

**`GetArrangementList()` can be empty, and then there is nowhere to arrange to.** The
shipped `Genesis8_Female.avt` loads with no arrangement points, and
`ImportExportOption.bAddArrangementPoints = True` does not add any — measured, the list
stays `[]`. `SetArrangement(piece, 0)` then does nothing, while `GetArrangementOfPattern`
still answers with a full-looking record (`{'ArrangementName': 'Arrangement Point',
'ArrangementOffsetX': '50', ...}`), so the readback does not reveal it either.
Arrangement points come with Library avatars, which this edition does not have. Without
them a garment can still be drafted, sewn, simulated and exported — it simply falls
rather than draping on the body.

---

## Fabric and materials

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

**`GetPatternInputInformation`'s `PointList[0]` is a header, not a point.** It is
`{"Point count": "6"}`, and the six points follow at indices 1..6. Iterating the list
straight raises `KeyError: 'Point positionX'` on the first element.

`GetPatternInformation` returns id, name, uuid and thumbnail — **no geometry**, despite
its documentation. `GetPatternInputInformation` is the one with per-line and per-point
data, including line lengths and types.

Its `PointList` holds **only the straight points**: a 7-point contour with 3 spline
points reports 4. The interior of a sampled curve cannot be read back at all, so
verification from outside MD reaches a curve's endpoints and its total length and stops
there. A shape can be wrong in ways nothing but simulation or an eye will show.

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
scene rather than adding to it, and took ~2s. Nothing measurable was lost, so the format
is safe as the checkpoint between sessions.

**As a document** — `pattern_api.ExportPatternJSON(_path)` / `ImportPatternJSON(_path)`.
Measured round trip: two sewn panels exported, `NewProject`, re-imported — both pieces
back with their names, both seam groups back, and a 360 mm edge still exactly 360 mm.
Import **replaces** the scene.

The document is richer than the call API, which is the reason to know about it:

```
formatVersion, Unit ("mm"), generator
FabricList              FabricName, FabricUUID, FabricType, strBaseColorHexCode, …
PatternList[]           ID, Name, CurrentFabricUUID, fGrainlineAngle, IsClosed,
                        IsHalfSymmetric, ShapeInfo.LineList[].PointList[]
                          (ID, PointType "Straight"/…, GradingRuleID, Position{x,y}),
                        InternalLineList, NotchList, OuterSeamAllowanceList,
                        ButtonHeadList, ButtonHoleList, AnnotationList,
                        ArrangementPointDataMap
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

### Seeing the garment: `SetCamViewPoint`, not `SetViewPoint`

**`utility_api.SetCamViewPoint(n)` moves the 3D camera. `utility_api.SetViewPoint(n)`
does not.** The names are one word apart and only one of them does anything, which is
expensive to discover: without the right one there is no way to look at the back of a
garment, and the obvious-looking call fails silently.

`SetCamViewPoint(0..7)` gives eight distinct views — front, three-quarter, side and
round the back. Follow it with `utility_api.Refresh3DWindow()` and then
`export_api.ExportSnapshot3D(path)`. Verified by eye on 2026-09-19, 2026.0.315.

**Snapshots of an unchanged scene are never byte-identical.** Sixteen captures of one
garment at one camera position produced sixteen different SHA-256s, so comparing image
bytes reports "changed" every time and proves nothing. It is not a settling effect;
repeated `Refresh3DWindow` calls do not converge. **Compare the pictures, not their
hashes** — which for two `SetViewPoint` values that hashed differently turned out to be
the same view.

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
list · undo. Seam allowances, notches and button placement are not exposed *as calls*,
but are reachable through pattern JSON.

---

## Execution environment

MD's interpreter is **CPython 3.11.8**, with `socket`, `threading`, `http.server`,
`subprocess` and `ssl` importable.

**Python only runs while a script is running.** An MD API call from a background thread
does work — but only during a script. The moment the script returns, MD stops releasing
the GIL and every Python thread freezes where it stands: a thread ticked 20 times while
its script's main thread slept two seconds, and zero times afterwards, with `is_alive()`
still `True`. This corrects an earlier reading of the same measurement, which took "a
worker thread works" to mean a worker thread keeps working.

**Nothing calls Python back**, either: there is no idle, timer or event callback anywhere
in the API. So anything that must outlive a script has to *be* a script that does not
return — which is what the listener is, and why it pumps MD's message queue to keep MD
usable while it holds the main thread (`docs/adr/001-bridge-transport.md`).

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

The listener is a script MD loaded when **Start** was pressed. Editing
`md_mcp_listener.py` afterwards does not touch the running instance — and because Python
reads the file off disk when it formats a traceback, a failure then reports **the running
instance's line numbers beside the new file's source**. The two disagree, so the
traceback points at unrelated code: a division-by-zero inside a script was reported at
`line 228, in _run` with a bare `)` as the offending line.

That reads as a bug in the listener. It is not; it means the listener in MD predates the
file. Failure replies carry `plugin_version` and `plugin_build` for this reason, and the
bridge appends them to the error. If the build is not the one just installed, **Stop and
Start the listener** — the scene lives in MD, not in the listener, so restarting it
loses nothing.

### Registering a plugin

**`utility_api.RegisterPythonScript(_name, _path)` works from the Python Editor, and
the entry appears without restarting MD.** Run as a plain script there with nothing else
going, it put a working entry in the Plugin tab there and then — confirmed by eye on
2026.0.315, 2026-09-19.

**And it answers `False` when it is called from a script that is running inside another
one.** Four attempts through the bridge — whose serve loop holds the main thread, so
anything it runs is nested inside it — were all declined and none appeared: three names
at once through `RegisterPythonScriptW`, then one through `RegisterPythonScript`, then
the same one with the colon taken out of its name. Same session, same MD, minutes after
the Editor call that worked. So neither the wide twin, nor the name, nor the folder is
the variable; the nesting is what is left, and that is a strong inference and not a
controlled measurement. **Register from a top-level Run, and check the bool**: it is the
only signal MD gives, there is no reader for the plugin list, and treating `False` as
success means an entry nobody ever registers again.

`RegisterPythonScriptFolder(_name, _path)` takes a name and a directory and is **not
exercised** — whether it produces a group rather than loose entries is worth an hour to
whoever needs a submenu. Both calls have `…W` twins.

**Nothing unregisters.** There is no remover anywhere in the six modules and no reader
for the list either, so a registered entry cannot be counted, replaced or withdrawn from
Python — only removed by hand in Plug-in Manager. A registration is therefore permanent
from the API's point of view, which makes "have I already registered this?" a question
that has to be answered outside MD.

**A plugin clicked while another script is running executes *nested* inside it, on MD's
main thread.** Measured through the bridge on 2026.0.315: with the serve loop holding the
main thread and pumping messages, a script run at that moment reported
`threading.current_thread() is threading.main_thread()` and could read the running
listener's own state off `builtins`. Two things follow. A second script can reach and
change the first one's state, which is how a Stop button is possible at all — it sets a
flag and returns, and the loop below it acts on the flag when it resumes. And the first
script cannot act on anything until the second returns, which is why the clicked script
has to return promptly and why a modal dialog in it stops everything.

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
put ninety megabytes of PySide6 inside MD's interpreter for it.

**A Qt widget must be driven by whatever script holds the main thread.** MD only runs
Python while a script runs, so the panel is refreshed from the serve loop, which pumps
the Win32 queue and calls `QApplication.processEvents()`. Never `exec()`/`mainloop()`:
that would hold the thread for as long as the window was open.

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
installed.

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
