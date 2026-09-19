"""What MD's calls actually do, for the ones where the signature is not enough.

MD's bindings are pybind11 with no docstrings written, so ``md_api`` can report a call's
name, its overloads and their types, and nothing at all about what it means. That is the
gap this fills: a caller who searches for ``Simulate`` gets the signature *and* the fact
that it returns ``True`` for any argument, including the one that leaves the garment flat.

**These annotate, they never replace.** The live build is still the ground truth for what
exists and what it takes; a note is an observation attached to it, measured against
:data:`MEASURED_AGAINST` by building scenes in MD and reading them back. Every claim here
is written up at length in ``docs/md-conventions.md``.

Keys are exactly as ``md_api`` spells a call, ``module.Function``. A key naming something
this build does not expose is a typo or a call MD has renamed, and either way the note
would silently never appear -- ``tests/md_required/test_bridge_live.py`` fails on that
rather than letting it rot.

A ``…W`` call inherits the note measured on its narrow-string twin, keyed once. MD ships
a wide-string form of most path-taking calls (``ExportOBJW``, ``GetFabricNameW``), they
are the same function over a different string type, and a caller who reached for the
Unicode-safe one was the caller most likely to be handed a bare signature.

Kept deliberately short and only for calls where the obvious reading is wrong. This is not
a second copy of the documentation; it is the warning that arrives at the moment of use.
"""

from __future__ import annotations

#: The MD build every note below was measured against.
MEASURED_AGAINST = "2026.0.315"

#: ``module.Function`` -> what using it actually does.
NOTES: dict[str, str] = {
    # -- Drafting a piece ------------------------------------------------------------
    "pattern_api.CreatePatternWithPoints": (
        "Points are (x, y, type) with type 0 straight and 2 spline; 3 is bezier and "
        "unusable, since MD exposes no way to set tangent handles, and 1 is "
        "undocumented -- do not send it. The contour closes implicitly: repeat the "
        "first point as the last and the resulting lines cannot be addressed at all. "
        "Addressable lines equal the number of type-0 points, so a curve sampled at "
        "eight points is ONE line, and any point that must sew to two different "
        "counterparts has to be straight."
    ),
    "pattern_api.CreateInternalShapeWithPoints": (
        "Returns an index scoped to the parent pattern, not a scene-global one -- the "
        "second piece's shapes number from 0 again. Its '@return PatternIndex' is a "
        "copy-paste error."
    ),
    "pattern_api.DeletePoint": (
        "Declared DeletePoint(_poinIndex) -- MD's typo, not this file's. Harmless while "
        "arguments are passed positionally, which over this bridge they always are."
    ),
    "pattern_api.DistribueInternalLinesbetweenSegments": (
        "MD's own misspelling of Distribute, and the call resolves under no other "
        "spelling. Reproduce it verbatim."
    ),
    "pattern_api.GetPatternSize": (
        "Answered 0 for a three-piece scene, despite a docstring identical to "
        "GetPatternCount's. GetPatternCount() is the count."
    ),
    "pattern_api.DeletePatternPiece": (
        "Renumbers the survivors: delete index 1 of three and the piece above slides "
        "into it. Every pattern index held across a delete is stale -- re-read them."
    ),
    "pattern_api.SymmetryPatternPiece": (
        "Returns None, so the piece it just created cannot be addressed and nothing "
        "else reports it either. Anything that has to be sewn afterwards must be "
        "mirrored with CopyPatternPieceMove, which returns an index, plus "
        "FlipPatternPiece."
    ),
    "pattern_api.InstancePatternPiece": (
        "Returns None like SymmetryPatternPiece and the LayerClone* pair: no index for "
        "what it made. Use CopyPatternPieceMove when the result has to be sewn."
    ),
    # -- Simulation ------------------------------------------------------------------
    "utility_api.Simulate": (
        "Runs n solver steps and returns True for ANY n, including n=1, which leaves the "
        "garment flat and reports success. Steps accumulate across calls; a few hundred "
        "is a drape. Measure the scene, never the return value."
    ),
    "utility_api.SetSimulationQuality": (
        "Takes two ints, not one, matching GetSimulationQuality()."
    ),
    "pattern_api.SetAddlThicknessCollision": (
        "Its readback is spelled GetAddlThicknessCollisionValue -- there is no "
        "GetAddlThicknessCollision, which is the name it would be searched under, and "
        "its absence is why this reads as write-only like SetPatternFreeze. It is not: "
        "the value round-trips."
    ),
    "pattern_api.GetAddlThicknessCollisionValue": (
        "The readback for SetAddlThicknessCollision, and the Value suffix is the whole "
        "reason it is hard to find. Real per-piece state: 2.5 and 1.0 on two pieces of "
        "the same measured garment."
    ),
    "utility_api.GetSimulationQuality": (
        "Returns a pair, which is why SetSimulationQuality takes two ints and raises "
        "TypeError for one. Measured (0, 1) on a working scene."
    ),
    # -- Sewing ----------------------------------------------------------------------
    "pattern_api.AddSeamlinePairGroup": (
        "Returns False for a line or piece index that does not exist, but validates "
        "indices and not meaning: a seam joining a hem to a neckline is accepted and "
        "counted. Count what MD ended up with; the scene looking right is not evidence. "
        "The direction flags mean 'along increasing point index', so two "
        "counter-clockwise pieces meeting face to face take (True, False) -- both "
        "pairings are accepted, and (True, True) measured as a collapsed, bunched tube. "
        "Sewing an edge to itself raises a modal dialog, which freezes MD's main thread "
        "and with it this bridge."
    ),
    "pattern_api.GetSeamlinePairGroupListInPattern": (
        "Per-piece seam membership, which nothing else reports. Use it to find orphaned "
        "pieces and to check left/right symmetry by seam count."
    ),
    # -- Arrangement and avatars -----------------------------------------------------
    "pattern_api.SetArrangementPosition": (
        "Does nothing. Swept over five values with an avatar loaded, the garment moved "
        "7.7mm against an expected 377, while SetArrangementOrientation on the same "
        "piece moved it 342mm. The readback echoes the integer back, which is why it "
        "looks as though it worked."
    ),
    "pattern_api.SetArrangement": (
        "Does nothing, silently, when GetArrangementList() is empty -- which is how the "
        "shipped Genesis8_Female.avt loads, and "
        "ImportExportOption.bAddArrangementPoints = True does not add any. An imported "
        "avatar can carry them (109 named points on the measured scene), so read the "
        "list rather than assuming either way. Without points a garment still drafts, "
        "sews and simulates; it just falls instead of draping on the body."
    ),
    "pattern_api.GetArrangementList": (
        "Read this before arranging anything: an empty list means there is nowhere to "
        "arrange to, and SetArrangement then does nothing while the readbacks still "
        "answer. The shipped Genesis8_Female.avt loads with none and "
        "ImportExportOption.bAddArrangementPoints = True does not add any, while an "
        "imported avatar measured 109 named points -- so it is worth reading, not "
        "assuming."
    ),
    "pattern_api.GetArrangementOfPattern": (
        "Answers a full-looking record ('ArrangementName': 'Arrangement Point', offsets "
        "and all) whether or not the piece was ever arranged -- measured giving exactly "
        "that generic record for piece 0 of a scene whose GetArrangementList() held 109 "
        "named points, and giving it again on a scene with none. It cannot tell you "
        "whether an arrangement happened."
    ),
    "pattern_api.SetArrangementOrientation": (
        "Degrees, and the default is 180, not 0. Unlike SetArrangementPosition it really "
        "moves the piece."
    ),
    "pattern_api.SetArrangementShapeStyle": "Accepts only 'Flat' and 'Curved'.",
    "import_api.ImportAvatar": (
        "Needs an ApiTypes.ImportExportOption() as its second argument -- there is no "
        "one-argument form, so md_call cannot reach it and the option must be built in "
        "run_md_python. The avatar then appears in export_api.GetAvatarNameList(), not "
        "in anything on import_api."
    ),
    "export_api.GetAvatarNameList": (
        "Avatar readback lives on export_api despite the module's name, and so does "
        "GetAvatarCount. Searching import_api or utility_api for it finds nothing."
    ),
    # -- Fabric and materials --------------------------------------------------------
    "fabric_api.GetFabricCount": (
        "Unreliable: answers 0 for a new project that has a fabric, and answered 4 for a "
        "scene with four, so 0 is not the tell. GetFabricStyleNameList() is the real "
        "count and carries the names."
    ),
    "fabric_api.GetFabricStyleNameList": (
        "The count and the names, and the one that matched the Object Browser exactly "
        "when GetFabricCount did not. Check it after anything that claims to have added "
        "a fabric."
    ),
    "fabric_api.GetFabricName": (
        "Past the end of the list this returns '' instead of raising, so it cannot be "
        "used to probe for fabrics a count is hiding -- an empty name reads as 'a fabric "
        "with no name' and invents materials that are not there."
    ),
    "fabric_api.AddFabric": (
        "Takes a file path to a .zfab, not a name. Handed a plain string it adds nothing, "
        "raises nothing and returns 0 -- indistinguishable from having added a fabric at "
        "index 0. Check GetFabricStyleNameList() afterwards."
    ),
    "fabric_api.SetFabricPBRMaterialBaseColor": (
        "DANGER: a side index of 1 ended the MD process outright -- no dialog, no "
        "traceback, unsaved scene lost. Side 0 is safe, and reads at side 1 are fine. "
        "Save before writing the second side, if at all."
    ),
    "fabric_api.AssignFabricToPattern": (
        "Takes three ints, not two. A call written against a two-argument shape is an "
        "arity mistake, which MD reports as a signature error. With the right arity it "
        "still returned False in both argument orders on the measured scene, so it is "
        "not a way to bind a fabric either."
    ),
    "fabric_api.GetFabricIndexForPattern": (
        "Reports what SetPatternPieceFabricIndex recorded, including when the surface "
        "never rebound -- it agreed with the wrong answer on twelve pieces. Not evidence "
        "of what a piece renders as: recolour the fabric and look."
    ),
    "pattern_api.SetPatternPieceFabricIndex": (
        "The readback agreeing is not evidence the surface rebound. Recolour the fabric "
        "and look. If a surface will not take any fabric, check it is a pattern piece at "
        "all: topstitch geometry is generated by MD, is reached by no fabric call, and at "
        "a large width reads as webbing lying on the garment."
    ),
    "pattern_api.ExportObjectBrowserMaterialsList": (
        "Despite the name it exports nothing and takes no path: it returns a short string "
        "listing the scene's fabrics and trims. ~160 characters for a four-fabric scene, "
        "and it matched what MD actually drew when the counters did not."
    ),
    "utility_api.GetTrimStyleCount": (
        "Answered 0 for a scene whose Object Browser listed two trims. Like "
        "GetFabricCount, not a counter to trust."
    ),
    # -- Trims and topstitching ------------------------------------------------------
    "pattern_api.AddSegmentTopstitch": (
        "Returns True and adds geometry MD generates itself: it is not a pattern piece, "
        "no fabric call reaches it, and GetPatternAssignedTopstitchCount is its only "
        "readback. At a large width it stops reading as stitching and starts reading as "
        "tan webbing lying on the garment -- 188 topstitch groups against 56 piece "
        "groups on the measured scene."
    ),
    "pattern_api.GetPatternAssignedTopstitchCount": (
        "The one countable readback in the trim family, and the way to tell generated "
        "topstitch geometry from pattern pieces when a surface will not take a fabric."
    ),
    "pattern_api.SetPatternPieceElastic": (
        "Accepted, returns None, and has no getter anywhere -- 'accepted' is the whole of "
        "what can be said, and nothing here claims it took effect. _lineIndex == -1 means "
        "every edge of the piece, so an off-by-one that produces -1 silently does the "
        "whole piece."
    ),
    "pattern_api.SetPatternPieceElasticStrength": (
        "No getter, and _lineIndex == -1 is every edge of the piece -- see "
        "SetPatternPieceElastic."
    ),
    "pattern_api.SetPatternPieceShirring": (
        "No getter, and _lineIndex == -1 is every edge of the piece -- see "
        "SetPatternPieceElastic."
    ),
    "pattern_api.SetPatternPieceSeamtaping": (
        "No getter, and _lineIndex == -1 is every edge of the piece. The width setter is "
        "spelled SetPatternPieceSametapingWidth -- MD's own typo, and it resolves under "
        "no other spelling."
    ),
    "pattern_api.SetPatternPieceSametapingWidth": (
        "MD's own typo, and the only spelling that resolves: this is the seamtaping "
        "width setter, and SetPatternPieceSeamtapingWidth does not exist. The piece "
        "setter beside it is spelled correctly, SetPatternPieceSeamtaping."
    ),
    # -- Seeing the scene ------------------------------------------------------------
    "utility_api.SetCamViewPoint": (
        "This is the one that moves the 3D camera -- use it, not SetViewPoint, to see "
        "the back of a garment. 0..7 give eight distinct views. Follow with "
        "Refresh3DWindow before a snapshot."
    ),
    "utility_api.SetViewPoint": (
        "Does NOT move the camera; measured PNG-size spread 0.0% across every value. "
        "SetCamViewPoint(n) is the one that does."
    ),
    "utility_api.GetViewPoint": "Answers -1 whatever was set.",
    "utility_api.Refresh3DWindow": (
        "Call it between a camera move and a snapshot -- but it does not settle the 3D "
        "window: repeated calls never make ExportSnapshot3D produce the same bytes twice."
    ),
    "utility_api.Set3DGarmentRenderingStyle": (
        "Does not turn off the 3D window's layer colour-coding: 0, 1 and 2 all left "
        "pieces on layers 1-2 lime and 3-4 tan. When colours look wrong, check "
        "GetPatternLayer before suspecting the fabrics."
    ),
    "export_api.ExportSnapshot3D": (
        "The snapshot call that works, and it returns a list of lists -- "
        "[['C:/.../shot.png']], not a path. Hashing one is evidence in neither "
        "direction: 16 captures of a draped garment at a fixed camera gave 16 digests, "
        "and repeated Refresh3DWindow never converges, while three captures of an empty "
        "scene and three of two sewn panels before simulating were byte-identical. Cloth "
        "makes the bytes move; stable bytes do not mean the scene held still. Compare "
        "the pictures."
    ),
    "export_api.ExportTurntableImages": (
        "Returns [] and writes no files. To get views round a garment, step "
        "SetCamViewPoint and call ExportSnapshot3D at each one."
    ),
    "export_api.ExportCustomViewSnapshot": (
        "Returns [] and writes no files, like ExportTurntableImages. ExportSnapshot3D is "
        "the one that produces an image."
    ),
    "export_api.ExportOBJ": (
        "Pass the ImportExportOption overload. The bare-path form ExportOBJ(path) gave no "
        "reply in 300s and wrote zero bytes on a scene the two-argument form exported in "
        "4.7s and 108MB. The exported group names also say what every surface really is, "
        "which is the fastest way to tell a pattern piece from generated geometry."
    ),
    # -- Destructive and one-way -----------------------------------------------------
    "utility_api.NewProject": (
        "Discards the open scene without asking, and MD's API has no undo anywhere. Save "
        "first."
    ),
    "utility_api.DisplayMessageBox": (
        "Raises a modal dialog, which holds MD's main thread -- the same thread that "
        "serves this bridge. Everything stops until somebody clicks OK in MD, and nothing "
        "times out at MD's end. Never call it from a script running over the bridge."
    ),
    "pattern_api.SetPatternFreeze": (
        "Write-only: there is no getter anywhere in the six modules, so a script cannot "
        "record the freeze state it is about to change and cannot put it back. With no "
        "undo either, this is a one-way edit -- reload the project if it mattered."
    ),
    "pattern_api.SetPatternLayer": (
        "Simulation layers hold trims off the garment surface and are not a finishing "
        "state: pieces left on layers 1-4 read as straps and pockets floating, which "
        "looks like a sewing bug. The 3D window also colour-codes by layer, so it reads "
        "as a material bug too. Move finished trims to layer 0 and re-simulate."
    ),
    "pattern_api.GetPatternLayer": (
        "Check this before blaming the fabrics for a colour problem: the 3D window "
        "colour-codes by layer -- 1-2 lime, 3-4 tan -- whatever fabric a piece carries, "
        "and Set3DGarmentRenderingStyle will not turn it off."
    ),
    # -- Reading a scene back --------------------------------------------------------
    "pattern_api.GetPatternInformation": (
        "Returns a JSON *string*, not a dict -- json.loads it. Carries id, name, uuid, "
        "thumbnail, isTrimItem and isSeamlessBlock, and no geometry at all despite its "
        "documentation. GetPatternInputInformation is the one with per-line and "
        "per-point data."
    ),
    "pattern_api.GetPatternInputInformation": (
        "Returns a JSON *string*, not a dict -- json.loads it, then read "
        "d['Pattern InputInformation'], whose key really does contain a space. Each "
        "record holds Pattern index, Pattern name, LineList and PointList, and element "
        "[0] of both lists is a header ({'Point count': '8'}) rather than data: iterate "
        "from 1 or every piece gains a phantom point and a phantom line. PointList holds "
        "only the straight points -- a 7-point contour with 3 spline points reports 4 -- "
        "so a sampled curve's interior cannot be read back at all. 'Line type' is "
        "'Straight type' for a straight line and '' for a curved one."
    ),
    "pattern_api.GetPatternPieceSolidifyStrengthen": (
        "Not broken: 534070.75 is the value for 'never set'. All six setters round-trip."
    ),
    # -- Documents and round trips ---------------------------------------------------
    "pattern_api.ExportPatternJSON": (
        "The document is richer than the call API, and that is the reason to know about "
        "it: notches, seam allowances and button placement have a field each and no "
        "setter anywhere in the six modules. A seam there is an arc-length span, not a "
        "line index. MD validates none of what it reads -- an invented field name was "
        "accepted and the omitted fields read from uninitialised memory -- so edit a "
        "document MD exported rather than authoring one from nothing."
    ),
    "pattern_api.ImportPatternJSON": (
        "Replaces the open scene rather than adding to it. The round trip measured "
        "clean: names, seam groups and a 360mm edge all came back exact."
    ),
    "export_api.ExportZPrj": (
        "The checkpoint that survives a round trip: a 56-piece scene read back through "
        "ImportZprj with identical names, fabric indices, layers, seam-group count and "
        "fabric list. Prefer the W twin for paths with non-ASCII characters."
    ),
    "import_api.ImportZprj": (
        "Replaces the open scene rather than adding to it, and takes an "
        "ApiTypes.ImportZPRJOption() -- so md_call cannot reach it; use run_md_python. "
        "~2s and nothing measurable lost on a 56-piece garment."
    ),
    "ApiTypes.ImportExportOption": (
        "The option object ExportOBJ, ImportAvatar and 35 other calls require. It has no "
        "JSON form, so md_call cannot build one -- construct it in run_md_python. The "
        "overload that omits it is not the convenient version: for ExportOBJ it is the "
        "one that never returns."
    ),
    "ApiTypes.ImportZPRJOption": (
        "The option ImportZprj requires. No JSON form, so md_call cannot build one -- "
        "construct it in run_md_python. bAppend defaults to False, which is why an "
        "import replaces the open scene instead of adding to it."
    ),
    "ApiTypes.ImportDxfOption": (
        "Vestigial: no function in this build takes it, and no signature anywhere "
        "mentions DXF. .zprj, .zpac and pattern JSON are the formats that move a pattern "
        "in and out."
    ),
    "ApiTypes.ExportDxfOption": (
        "Vestigial -- nothing in the build takes it, and there is no ExportDXF call. See "
        "ImportDxfOption."
    ),
    # -- UI from a plugin ------------------------------------------------------------
    "utility_api.RegisterWidget": (
        "Accepted and does nothing visible: handed shiboken6.getCppPointer(w)[0] it "
        "returns without complaint and MD survives, but the widget is not reparented, "
        "docked or shown. With DeleteWidgets and ResetWidgetRegistry beside it, it reads "
        "as ownership bookkeeping rather than a way into MD's layout -- a plugin can have "
        "a Qt window of its own and cannot dock one."
    ),
    "utility_api.DeleteWidgets": (
        "The other half of RegisterWidget's bookkeeping, and unexercised. RegisterWidget "
        "itself does nothing visible, so there is nothing measured for this to undo."
    ),
    "utility_api.ResetWidgetRegistry": (
        "Unexercised, like DeleteWidgets, and only as meaningful as RegisterWidget -- "
        "which is accepted and does nothing visible."
    ),
    "utility_api.UpdateCloStyleForPlugIn": (
        "Unreachable from Python: MD's pybind11 binding has no caster for PySide6 "
        "objects, so a shiboken QWidget is refused with 'incompatible function "
        "arguments'. Nothing is lost -- a widget created in MD's own QApplication "
        "already wears MD's palette and font."
    ),
    "utility_api.GetStyleSheetCodeForWidget": (
        "Uncallable: its argument is a Marvelous::CloWidgetType, that enum is exposed "
        "nowhere in ApiTypes, and an int is refused -- an API with no way to build its "
        "argument. MD's app-level stylesheet is one line anyway; the look is in the "
        "palette."
    ),
    "utility_api.CreateProgressBar": (
        "Present and unexercised, with SetProgress and DeleteProgressBar. Whatever it "
        "puts up, MD runs Python only while a script runs, so nothing here may hold the "
        "main thread: no exec(), no mainloop(), and a modal dialog freezes this bridge."
    ),
    "utility_api.SetProgress": (
        "Present and unexercised -- see CreateProgressBar."
    ),
    "utility_api.DeleteProgressBar": (
        "Present and unexercised -- see CreateProgressBar."
    ),
    # -- Environment -----------------------------------------------------------------
    "utility_api.RegisterPythonScriptFolder": (
        "Takes a name and a directory, and is unexercised -- whether it makes a submenu "
        "or loose entries is unknown. The registration constraint on "
        "RegisterPythonScript applies: run it from a top-level Run in MD's Python "
        "Editor, never from a script nested inside another. Nothing anywhere "
        "unregisters, and nothing reads the plugin list back, so a registration is "
        "permanent from the API's side."
    ),
    "utility_api.RegisterPythonScript": (
        "Declared -> bool, and answers False for every name and path when called from a "
        "script running inside another one -- which is what a call over this bridge "
        "always is. Register from a top-level Run in MD's Python Editor instead, and "
        "never record the False as success."
    ),
}


def _narrow_twin(call: str) -> str | None:
    """The narrow-string call a ``…W`` name is the wide twin of, if it is a measured one.

    Every one of this build's 82 ``…W`` members has a narrow counterpart, so the trailing
    W is the twin marker and not part of a name -- but the test is still whether the stem
    is something measured, not whether the name ends in a letter. A call that merely ends
    in W and has no note keeps none.
    """
    if not call.endswith("W"):
        return None
    stem = call[:-1]
    return stem if stem in NOTES else None


def note_for(call: str) -> str | None:
    """What is known about ``call``, or ``None`` if nothing is.

    A wide-string twin answers with the note measured on its narrow form, said out loud:
    the text names the call it was measured on, and silently handing it over would read
    as a claim that ``ExportOBJW`` itself is what hung for 300s.
    """
    note = NOTES.get(call)
    if note is not None:
        return note

    twin = _narrow_twin(call)
    if twin is None:
        return None
    return f"Measured on {twin.split('.', 1)[1]}, the narrow-string twin. {NOTES[twin]}"


def annotate(entries: list[dict]) -> list[dict]:
    """Attach a ``note`` to each entry that has one, in place, and return them.

    Entries MD reported are never dropped, reordered or altered otherwise: an annotation
    is added beside what the live build said, and calls with nothing measured stay exactly
    as they arrived.
    """
    for entry in entries:
        note = note_for(entry.get("call", ""))
        if note:
            entry["note"] = note
    return entries
