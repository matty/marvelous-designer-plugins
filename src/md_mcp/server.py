"""The MCP server: Marvelous Designer's whole API, over the live bridge.

Four tools reach the API. ``md_status`` says whether MD is reachable, ``md_api``
searches everything the running build exposes, ``md_call`` calls any of it, and
``run_md_python`` runs a script inside MD for work that is several calls deep. There is
no curated subset and no wrapper per feature: the API MD ships is the API this serves.

The rest read back what that API cannot, or keep a scene recoverable: ``md_measure``
(3D extents, from OBJ exports), ``md_arrangement_points`` (where arrangement points
really are), ``md_snapshot`` (pictures of the 3D window), ``md_fabric_presets`` (the
``.zfab`` files ``AddFabric`` needs), ``md_seams`` (what is sewn to what),
``md_checkpoint`` and ``md_restore`` (the only undo), and ``md_guide`` (measured facts
about MD, which the instructions have no room for). They add no calls to MD; each is a
script run through the same bridge.

Everything here is a thin shell. The socket is in ``md_mcp.bridge``; the half that runs
inside MD is ``plugin/md_mcp_listener.py``.

Tools **fail** rather than returning an empty result when MD is not listening. A caller
that read "no patterns found" as success would conclude MD is connected and idle, which
is the opposite of true.
"""

from __future__ import annotations

import functools
import json
import os
import pathlib
import struct
import sys
import tempfile
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from md_mcp import (
    __version__, bridge, checkpoints, dialogs, drafting, fit, geometry, guide, seams, sewing,
)


def _reportable(fn: Callable) -> Callable:
    """Let a tool's own message reach the caller.

    The SDK replaces the text of any exception a tool raises with a generic one, except
    for ``ToolError`` -- everything else is treated as a crash whose detail stays on the
    server. Every error raised below names what the caller got wrong or what to do next,
    so it is worth nothing unless it arrives.

    Only the two deliberate kinds are converted: a rejected argument (``ValueError``) and
    a bridge that could not deliver (``BridgeError``, which already wraps every socket
    error). Anything else from inside md-mcp is a bug, and must keep its traceback and
    reach the caller as a crash -- dressed up as a ToolError it reads like bad input, and
    the model retries it forever.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        try:
            return fn(*args, **kwargs)
        except (ValueError, bridge.BridgeError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


#: What MD reports back about the scene, beyond the pattern pieces themselves. Kept in
#: one place because ``md_status`` and ``run_md_python`` must report the same thing: a
#: caller that saw a seam count from one and not the other would read the silence as
#: zero.
_SCENE_KEYS = (
    "pattern_count",
    "pieces",
    "seam_group_count",
    "avatars",
    "fabrics",
    "readback_errors",
)


def _scene_summary(scene: dict) -> dict:
    """The scene keys MD actually answered, dropping the ones it could not."""
    return {key: scene[key] for key in _SCENE_KEYS if key in scene}


def _write_fields(writes: list | None, function: str | None = None) -> dict:
    """What the listener found when it checked the files an export said it wrote.

    MD's return value is not evidence of a write: measured on 2026.0.315, ExportOBJ into
    a folder that did not exist returned the path list of a successful export and wrote
    nothing. So a path that is not there is said in so many words, beside the result
    that claimed it.
    """
    if writes is None:
        if function is not None and bridge.is_write_call(function):
            return {
                "write_check": (
                    "unverified: the listener running in MD predates write checking, "
                    "so nobody looked for the file. Read it back before relying on it; "
                    "reloading the listener (md_status names the running build) adds "
                    "the check."
                )
            }
        return {}
    if not writes:
        return {}
    fields: dict[str, Any] = {"writes": writes}
    missing = [path for record in writes for path in record.get("missing_outputs", [])]
    empty = [path for record in writes for path in record.get("empty_outputs", [])]
    created = [path for record in writes for path in record.get("created_folders", [])]
    if missing:
        fields["missing_outputs"] = missing
    if empty:
        fields["empty_outputs"] = empty
    if created:
        fields["created_folders"] = created
    if missing or empty:
        fields["warning"] = (
            "MD answered as though it had written these files, and "
            + ("they are not there" if missing else "they are empty")
            + ". Treat the export as failed: "
            + ", ".join(missing + empty)
        )
    return fields


# ------------------------------------------------------------------------ images

#: Snapshots are scaled so their long edge is at most this, unless asked otherwise. An
#: image stays in the conversation; MD's 3D window on a large display is several times
#: this and several megabytes.
SNAPSHOT_MAX_SIZE = 1024

#: What each SetCamViewPoint number shows: by eye on 2026.0.315 (2026-10-11, the Nardo
#: avatar), and as MD's own API list documents it. 7 and 9 keep the direction the camera
#: already had -- after the front view, 7 zoomed in on the front and 9 framed it whole.
CAMERA_VIEWS = {
    0: "bottom",
    1: "three-quarter right",
    2: "front",
    3: "three-quarter left",
    4: "right",
    5: "top",
    6: "left",
    7: "focus zoom, same direction",
    8: "back",
    9: "zoom to fit, same direction",
}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _image_format(data: bytes) -> str | None:
    if data.startswith(_PNG_SIGNATURE):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpeg"
    return None


def _png_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from a PNG's header, or None if this is not a PNG."""
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _shrink_png(data: bytes, max_size: int) -> bytes:
    """Scale a PNG so its long edge is ``max_size``, with Windows' own GDI+.

    md-mcp runs beside MD, and MD runs on Windows, so the scaler that is always there
    is the one Windows ships -- rather than a dependency for one tool. Anywhere else
    this raises OSError and the caller sends the image at full size.
    """
    if sys.platform != "win32":
        raise OSError("scaling uses Windows GDI+, and this is not Windows")
    import ctypes
    from ctypes import wintypes

    class StartupInput(ctypes.Structure):
        _fields_ = [
            ("GdiplusVersion", ctypes.c_uint32),
            ("DebugEventCallback", ctypes.c_void_p),
            ("SuppressBackgroundThread", wintypes.BOOL),
            ("SuppressExternalCodecs", wintypes.BOOL),
        ]

    class Guid(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    gdi = ctypes.WinDLL("gdiplus")
    handle = ctypes.c_void_p
    out = ctypes.POINTER(ctypes.c_void_p)
    number = ctypes.c_int
    gdi.GdiplusStartup.argtypes = [
        ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(StartupInput), ctypes.c_void_p
    ]
    gdi.GdiplusShutdown.argtypes = [ctypes.c_size_t]
    gdi.GdiplusShutdown.restype = None
    gdi.GdipLoadImageFromFile.argtypes = [ctypes.c_wchar_p, out]
    gdi.GdipGetImageWidth.argtypes = [handle, ctypes.POINTER(ctypes.c_uint)]
    gdi.GdipGetImageHeight.argtypes = [handle, ctypes.POINTER(ctypes.c_uint)]
    gdi.GdipCreateBitmapFromScan0.argtypes = [
        number, number, number, number, ctypes.c_void_p, out
    ]
    gdi.GdipGetImageGraphicsContext.argtypes = [handle, out]
    gdi.GdipSetInterpolationMode.argtypes = [handle, number]
    gdi.GdipSetPixelOffsetMode.argtypes = [handle, number]
    gdi.GdipDrawImageRectI.argtypes = [handle, handle, number, number, number, number]
    gdi.GdipDeleteGraphics.argtypes = [handle]
    gdi.GdipSaveImageToFile.argtypes = [
        handle, ctypes.c_wchar_p, ctypes.POINTER(Guid), ctypes.c_void_p
    ]
    gdi.GdipDisposeImage.argtypes = [handle]

    def check(status: int, what: str) -> None:
        if status:
            raise OSError(f"GDI+ {what} failed with status {status}")

    pixel_format_32bpp_argb = 0x0026200A
    high_quality_bicubic = 7
    high_quality_offset = 2
    png_encoder = Guid(
        0x557CF406, 0x1A04, 0x11D3,
        (ctypes.c_ubyte * 8)(0x9A, 0x73, 0x00, 0x00, 0xF8, 0x1E, 0xF3, 0x2E),
    )

    token = ctypes.c_size_t()
    check(
        gdi.GdiplusStartup(
            ctypes.byref(token), ctypes.byref(StartupInput(1, None, False, False)), None
        ),
        "startup",
    )
    try:
        with tempfile.TemporaryDirectory(prefix="md-mcp-image-") as folder:
            source = os.path.join(folder, "full.png")
            target = os.path.join(folder, "scaled.png")
            pathlib.Path(source).write_bytes(data)
            image = ctypes.c_void_p()
            check(gdi.GdipLoadImageFromFile(source, ctypes.byref(image)), "load")
            try:
                width, height = ctypes.c_uint(), ctypes.c_uint()
                check(gdi.GdipGetImageWidth(image, ctypes.byref(width)), "width")
                check(gdi.GdipGetImageHeight(image, ctypes.byref(height)), "height")
                scale = max_size / max(width.value, height.value, 1)
                new_width = max(1, round(width.value * scale))
                new_height = max(1, round(height.value * scale))
                bitmap = ctypes.c_void_p()
                check(
                    gdi.GdipCreateBitmapFromScan0(
                        new_width, new_height, 0, pixel_format_32bpp_argb, None,
                        ctypes.byref(bitmap),
                    ),
                    "bitmap",
                )
                try:
                    graphics = ctypes.c_void_p()
                    check(
                        gdi.GdipGetImageGraphicsContext(bitmap, ctypes.byref(graphics)),
                        "graphics",
                    )
                    try:
                        gdi.GdipSetInterpolationMode(graphics, high_quality_bicubic)
                        gdi.GdipSetPixelOffsetMode(graphics, high_quality_offset)
                        check(
                            gdi.GdipDrawImageRectI(
                                graphics, image, 0, 0, new_width, new_height
                            ),
                            "draw",
                        )
                    finally:
                        gdi.GdipDeleteGraphics(graphics)
                    check(
                        gdi.GdipSaveImageToFile(
                            bitmap, target, ctypes.byref(png_encoder), None
                        ),
                        "save",
                    )
                finally:
                    gdi.GdipDisposeImage(bitmap)
            finally:
                gdi.GdipDisposeImage(image)
            return pathlib.Path(target).read_bytes()
    finally:
        gdi.GdiplusShutdown(token)


#: What each tool does to MD, for clients that act on MCP's tool annotations -- an
#: unannotated tool is presented as destructive and non-idempotent.
READS = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
#: Leaves the garment as it was, but not everything: moves the camera, adds and removes
#: a probe piece, or writes a checkpoint file.
TOUCHES = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
#: Runs whatever it is given inside MD, which has no undo.
CHANGES = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)
#: Adds to the garment -- a piece, a seam -- and changes nothing already there. MD has
#: no call that removes a seam, so a wrong one is undone by md_restore.
BUILDS = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
#: Discards the open garment.
REPLACES = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)


def build_server() -> MCPServer:
    server = MCPServer(
        name="md-mcp",
        version=__version__,
        instructions=(
            "Drives a running Marvelous Designer. Call md_guide before changing "
            "anything: short, measured facts about how MD behaves. md_api searches "
            "everything this "
            "build of MD exposes, md_call calls any of it, run_md_python runs a whole "
            "script inside MD. Search before calling: the search gives the exact "
            "spelling and arity, and MD dispatches its overloads on arity. Lengths are "
            "millimetres and there is no undo. MD must be open with the bridge "
            "started (Plugin tab > Start MCP).\n"
            "To build, md_draft_piece drafts a piece from named edges and md_sew sews "
            "two of them by name ('front.side'), saying which ends meet; both check "
            "what MD built. The raw calls stay available for everything else.\n"
            "MD fails quietly more often than it fails loudly, so read the scene back "
            "rather than trusting a return value. Three that were measured on "
            "2026.0.315: utility_api.Simulate(n) runs n solver steps and returns True "
            "for any n, so Simulate(1) leaves the garment flat -- it needs a few "
            "hundred, and repeated calls continue from where the last left off. A "
            "sewing call naming a missing line or piece returns False; valid indices "
            "can still pair the wrong edges, and Layer Clone sews every edge on its "
            "own. So read seams back with md_seams after sewing: it names the lines "
            "each seam joins and warns on an edge sewn twice or to itself. Plan pairs "
            "before sewing; verify lengths, ease and direction; counts alone prove "
            "nothing. When a surface will not take a fabric, check it is a pattern "
            "piece: measured 2026-09-19, strap-shaped geometry that was not one "
            "ignored every fabric change.\n"
            "The call API cannot see the result in 3D, so these read it back: "
            "md_measure (avatar and garment bounds, avatar slices at given heights), "
            "md_arrangement_points (where arrangement points really are -- their names "
            "can be a metre out), md_snapshot (a picture of the 3D window) and "
            "md_fabric_presets (the .zfab paths AddFabric needs). Exports are checked "
            "for the files they claim: missing_outputs means MD reported a file it did "
            "not write."
        ),
    )

    @server.tool(annotations=READS)
    @_reportable
    def md_status() -> dict:
        """Check that Marvelous Designer is reachable, and say which build it is.

        Ask first in a session: every other tool fails without the listener, and this
        says so in one call rather than in the middle of some real work.

        ``plugin_version`` and ``plugin_path`` are the listener that answered, which
        is not necessarily the newest one installed: MD holds a registered plugin's
        path for good.

        Also reports the scene MD currently holds -- pattern pieces, seam groups,
        avatars and fabrics -- all read out of MD rather than remembered. ``pieces``
        and ``seam_group_count`` are worth checking after any build: a sewing call
        returns False for missing indices, and valid indices can still join the wrong
        edges. Inspect the scene as well as the count.
        """
        reply = bridge.ping()
        scene = bridge.scene()
        status = {
            "connected": True,
            "md_version": reply.get("md_version"),
            "port": bridge.port(),
            # Which listener answered, not which one is on disk. MD keeps a registered
            # plugin's path for good and an old install does not stop existing, so an
            # MD serving a version behind is a real and otherwise invisible state --
            # every symptom of it looks like the bridge lying about MD.
            "plugin_version": reply.get("plugin_version"),
            "plugin_path": reply.get("plugin_path"),
            "plugin_build": reply.get("plugin_build"),
        }
        status.update(_scene_summary(scene))
        # Read from md-mcp's side, so it answers even when MD cannot: a modal dialog
        # holds the thread that serves the bridge.
        status["dialogs"] = dialogs.showing()
        return status

    @server.tool(annotations=READS)
    @_reportable
    def md_api(search: str = "", query: str = "") -> dict:
        """Search everything the running Marvelous Designer exposes.

        search: words that must all appear in the call name, case-insensitive, so
            (``query`` is accepted as the same argument)
            "pattern piece fabric" finds exactly the two calls that name all three.
            Try "Pattern", "Seamline", "Avatar", "Simulate", "Export", "Fabric",
            "Arrangement". Empty lists every call by name, without signatures, as an
            index; pass a term to get the signatures for what it matches.

        Returns each call as "module.Function" with every available signature. Option
        structs include field names and default values. Read out of the live build, not
        from a captured list: MD's published docs have been wrong on several points, and
        the running host is the only ground truth. Feed a name straight to md_call.

        MD's bindings carry signatures and almost no prose, so a signature tells you a
        call's shape and nothing about its meaning. Where a call's behaviour has been
        measured, the result also carries a "note" saying what using it actually does --
        that Simulate returns True for the argument that leaves the garment flat, that
        writing colour side 1 has ended the MD process. Read the note before calling:
        the calls worth warning about are the ones whose signature reads as reassuring.
        """
        # ``query`` is the same argument under the name callers reach for first. An
        # MCP client drops an argument the tool did not declare rather than refusing
        # the call, so a caller who says query= silently searched for "" and got all
        # 688 calls -- ~99KB spent on a synonym. Accepting both is cheaper than being
        # right about which name it should have been.
        term = search or query
        found = bridge.api(term)
        return {"search": term, "count": len(found), "calls": found}

    @server.tool(annotations=CHANGES)
    @_reportable
    def md_call(function: str, args: list | None = None) -> dict:
        """Call one Marvelous Designer API function and return what it returned.

        function: "module.Function" as md_api spells it, e.g. "utility_api.Simulate".
            No parentheses.
        args: positional arguments, in order. MD dispatches its overloads on arity and
            argument type, so pass exactly what the signature says and never a
            placeholder None.

        Lengths are millimetres. A few calls hand back objects with no JSON equivalent;
        those arrive as their repr rather than failing the call.

        An Export* call (ExportZPrj, ExportSnapshot3D, ExportPatternJSON, ...) is checked
        after it returns: ``writes`` says which of the paths it named or returned exist,
        and ``missing_outputs`` plus a ``warning`` appear when MD claimed a file that is
        not there -- measured: ExportOBJ into a missing folder returned its path list
        and wrote nothing. A missing parent folder of an absolute path is created first
        and reported under ``created_folders``.
        """
        result, writes, showing = bridge.call_checked(function, args or [])
        reply = {"function": function, "result": result}
        reply.update(_write_fields(writes, function))
        if showing:
            reply["dialogs"] = showing
            reply["dialog_warning"] = dialogs.describe(showing)
        return reply

    @server.tool(annotations=CHANGES)
    @_reportable
    def run_md_python(source: str, timeout_seconds: float | None = None) -> dict:
        """Run a Python script inside Marvelous Designer and report what it printed.

        For work that is several calls deep, or needs a loop, a returned index or a
        condition -- drafting a pattern piece and sewing it, say, where each create call
        returns the index the next one needs. `pattern_api`, `utility_api`, `export_api`,
        `import_api` and `fabric_api` are importable in MD.

        timeout_seconds: how long to wait for the reply, default 300. Raise it for a
            long drape or a large export; lower it to probe a call that may hang, which
            is the only way to tell "MD is still building" from "MD is stuck" without
            spending the full default finding out. Whatever this is set to, it abandons
            the *reply* and never the work -- MD runs the script to the end either way,
            so read the scene back before retrying rather than assuming nothing landed.

        Returns the script's own output plus the scene MD ended up with -- pieces, seam
        groups, avatars, fabrics -- read back from MD rather than echoed. Check them:
        a sewing call returns False for missing indices, but can accept the wrong
        pair of valid edges. Check ``seam_group_count`` and inspect the resulting scene.

        An exception includes its traceback, captured output and available partial
        scene. Earlier edits are not rolled back: inspect before retrying. A successful
        script with unavailable scene fields remains successful and reports those
        fields under readback_errors. elapsed_seconds measures script execution only.
        Each run returns the complete available piece list, including across clients.
        The listener keeps serving after a script exception.

        Every Export* call the script makes is checked as it returns. ``writes`` lists
        what was found; ``missing_outputs`` and a ``warning`` mean MD reported a file
        that is not there, and ``created_folders`` names any missing parent folder made
        so an export could land.
        """
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ToolError(
                f"timeout_seconds must be greater than 0, not {timeout_seconds!r}. "
                "Omit it for the 300s default."
            )
        reply = bridge.run_script(
            source, name="<md-mcp tool>", timeout=timeout_seconds
        )
        result = {"output": reply.get("output", "")}
        for key in ("execution_succeeded", "elapsed_seconds"):
            if key in reply:
                result[key] = reply[key]
        result.update(_scene_summary(reply))
        result.update(_write_fields(reply.get("writes")))
        if reply.get("writes_omitted"):
            result["writes_omitted"] = reply["writes_omitted"]
        for key in ("dialogs", "dialog_warning"):
            if key in reply:
                result[key] = reply[key]
        return result

    @server.tool(annotations=READS)
    @_reportable
    def md_measure(
        heights: list[float] | None = None,
        band_mm: float = 20.0,
        group_search: str = "",
        max_groups: int = 100,
    ) -> dict:
        """Measure the scene in 3D: the avatar's extent, each garment group's, and
        horizontal slices through the avatar.

        MD's call API has no 3D readback -- GetClothPositions cannot return anything to
        Python, and nothing reports an avatar's size or a piece's 3D bounds. This exports
        the avatar alone and the garment alone to OBJ in a temporary folder inside MD,
        reads them, and deletes them. The scene is not changed.

        heights: avatar heights in mm (Y, the floor is near 0) to slice at. Each slice
            reports the x and z range of the avatar vertices within band_mm of that
            height, and how many there were -- a tail or a hanging arm widens it.
        band_mm: thickness of each slice, default 20.
        group_search: words that must all appear in a garment group's name; the default
            lists every group.
        max_groups: how many garment groups to list (default 100); the rest are counted
            under groups_omitted.

        Returns avatar {names, vertices, min, max, size, groups}, cross_sections, and
        garment {vertices, min, max, size, groups_total, groups: [{name, vertices, min,
        max, centroid}]}, in mm. Axes as measured on 2026.0.315: Y up, +Z is the
        avatar's front. Group names are MD's: one per pattern piece was observed, named
        after it, and topstitching comes as Topstitch_* groups. A name that occurs twice
        is listed twice with an "occurrence" number, never merged; a file with no groups
        becomes one group called "garment". Bounds come from the export, so they show the
        cloth as last simulated.
        """
        if band_mm <= 0:
            raise ValueError(f"band_mm must be greater than 0, not {band_mm!r}")
        if max_groups < 0:
            raise ValueError(f"max_groups cannot be negative, not {max_groups!r}")
        return bridge.measure(heights or [], band_mm, group_search, max_groups)

    @server.tool(annotations=TOUCHES)
    @_reportable
    def md_arrangement_points(
        points: list[str | int] | None = None, timeout_seconds: float | None = None
    ) -> dict:
        """Find where the avatar's arrangement points really are, in 3D.

        GetArrangementList gives names and percentage offsets, never positions, and the
        names mislead: on a tailed avatar the body points sat about a metre behind the
        back (Body_Back_Center_1 at z = -1150 with the back at z = -116). The only way
        to know is to put something there and look, so this TEMPORARILY ADDS ONE
        PATTERN PIECE -- a 200 x 140 mm probe, created last -- arranges it at each point
        in turn, exports the garment to OBJ to see where it landed, and deletes it. The
        probe is deleted even when a point fails, and only once it has been found by
        its own name, so nothing of yours is deleted in its place.

        points: names or indices to resolve; default every point. A name that matches
            no point exactly is taken as a case-insensitive part of a name, so "Back"
            resolves every point with Back in its name. Unmatched entries are listed.
        timeout_seconds: default 300. Each point is one garment export, so a large
            scene and all of a hundred points can take minutes.

        Returns points: [{index, name, center, min, max}] in mm (Y up, +Z the avatar's
        front), failed: [{index, name, error}], and probe: the before/after pattern
        count, seam-group count and fabric list, "unchanged" when all three match, and
        pattern_count_read_back from the listener's own readback. If "unchanged" is
        false or the probe report has an "error", inspect the scene: something was left
        behind or removed. The centre is the probe's bounding-box centre; a point that
        wraps the piece round a limb puts it inside the limb, not on the skin.
        """
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be greater than 0, not {timeout_seconds!r}. "
                "Omit it for the 300s default."
            )
        return bridge.arrangement_points(points, timeout=timeout_seconds)

    def _picture(data: bytes, shot: dict, max_size: int | None) -> tuple[dict, Image]:
        """One snapshot, checked, described and scaled -- meta and image."""
        view = shot.get("view")
        kind = _image_format(data)
        if kind is None:
            raise bridge.BridgeError(
                "MD's snapshot is neither a PNG nor a JPEG; its first bytes were "
                f"{data[:8]!r}"
            )
        meta: dict[str, Any] = {
            "view": view,
            "facing": (
                "the 2D pattern: every piece's outline, as laid out on the board"
                if view == "pattern" else
                CAMERA_VIEWS.get(view, "wherever the camera already was")),
            "camera": "not moved: the pattern is drawn whole" if view == "pattern" else (
                f"moved to view {view} and left there: MD cannot read the camera "
                "back, so the previous view was not restored"
                if shot.get("camera_moved")
                else "not moved: this is the view MD was already showing"
            ),
            "format": kind,
        }
        size = _png_size(data)
        if size:
            meta["full_size"] = list(size)
        if max_size and size and max(size) > max_size:
            try:
                data = _shrink_png(data, max_size)
                meta["size"] = list(_png_size(data) or size)
            except (OSError, AttributeError) as exc:
                meta["size"] = list(size)
                meta["scaling_error"] = f"sent at full size: {exc}"
        elif size:
            meta["size"] = list(size)
        elif max_size:
            meta["scaling_error"] = f"sent as MD wrote it: only PNGs are scaled, not {kind}"
        meta["bytes"] = len(data)
        return meta, Image(data=data, format=kind)

    @server.tool(annotations=TOUCHES)
    @_reportable
    def md_snapshot(
        view: int | None = None,
        max_size: int | None = SNAPSHOT_MAX_SIZE,
        views: list[int] | None = None,
        pattern: bool = False,
    ) -> list[Image | dict]:
        """Look at MD's 3D window, or its 2D pattern: snapshots, returned as images.

        view: a camera position, 0 to 9, set with SetCamViewPoint. Measured on
            2026.0.315: 0 bottom, 1 three-quarter right, 2 front, 3 three-quarter left,
            4 right, 5 top, 6 left, 8 back; 7 zooms in and 9 zooms to fit, both keeping
            the direction the camera already had. Omit it to photograph the camera where
            it is. (SetViewPoint, one word away, is accepted and moves nothing.)
        views: several positions in one call, one image each, in this order --
            views=[2, 4, 8] is front, side and back, the quickest way to check a drape
            all round. Give view or views, not both.
        max_size: the longest edge of each image in pixels, default 1024, so they stay
            cheap to keep in the conversation; 512 is plenty for three views. 0 sends
            them at full size.
        pattern: also (or only, with no view) a picture of the 2D pattern -- every
            piece's outline, whole, as laid out on the board. The quickest way to judge
            a draft's proportions: a sleeve as wide as a back panel is visible at once.
            Thin grey lines, so keep max_size at 1024 or more.

        THE CAMERA IS LEFT WHERE THIS PUT IT. MD has no call that reads the camera back
        (GetViewPoint answers -1 whatever is set), so the previous view cannot be
        restored. Comparing snapshots by hash proves nothing either way -- a draped
        scene never gives the same bytes twice -- so compare the pictures.
        """
        if view is not None and views:
            raise ValueError("give view or views, not both")
        wanted: list[int | str | None] = list(views) if views else (
            [] if pattern and view is None else [view])
        for one in wanted:
            if one is not None and one not in CAMERA_VIEWS:
                raise ValueError(
                    f"a view must be 0 to 9 -- the ten SetCamViewPoint positions -- or "
                    f"omitted, not {one!r}"
                )
        if max_size is not None and max_size < 0:
            raise ValueError(f"max_size cannot be negative, not {max_size!r}")
        if pattern:
            wanted.append("pattern")
        pictures = [_picture(data, shot, max_size) for data, shot in bridge.snapshots(wanted)]
        if len(pictures) == 1:
            meta, image = pictures[0]
            return [meta, image]
        return [{"views": [meta for meta, _ in pictures]}] + [image for _, image in pictures]

    @server.tool(annotations=READS)
    @_reportable
    def md_fabric_presets(search: str = "") -> dict:
        """List the fabric presets on this machine: the .zfab files AddFabric needs.

        fabric_api.AddFabric takes a path to a .zfab, not a name -- handed anything else
        it returns 0, a plausible index, and adds nothing. MD lists its shipped presets
        nowhere in the API. This searches, from inside MD: the Preset folder of the
        running MD's own install (found from MD's interpreter, not a path written down),
        Public Documents' MarvelousDesigner folder (where "New Assets\\Fabric" lives),
        and the user's Documents\\MarvelousDesigner.

        search: words that must all appear in a file's full path, case-insensitive --
            "cotton", "denim heavy". The default lists everything.

        Returns folders: [{folder, source, fabrics: [{name, path}]}], count (listed),
        total (found before the search), and roots: every folder searched, whether it
        exists and how many .zfab files it held. Pass a path straight to
        fabric_api.AddFabric.
        """
        return bridge.fabric_presets(search)

    @server.tool(annotations=READS)
    @_reportable
    def md_guide(topic: str = "") -> dict:
        """How Marvelous Designer behaves through md-mcp: read it before changing anything.

        Short, measured facts -- what MD's return values do and do not prove, how seams,
        placement and saving really behave, and the workflow that keeps a scene
        recoverable. Each is backed by md-mcp's docs/md-conventions.md. Nothing in it is
        about designing garments.

        topic: "workflow", "readback", "seams", "placement", "saving" or "simulation".
            Leave it empty for all of it (about 4 kB).

        Returns topics (every topic there is) and text (markdown).
        """
        return guide.read(topic)

    @server.tool(annotations=TOUCHES)
    @_reportable
    def md_checkpoint(label: str = "") -> dict:
        """Save the garment to a checkpoint, so it can be put back with md_restore.

        MD's API has no undo and nothing removes a seam, so take one before any change
        you might want to take back -- sewing above all. Saved as a .zpac (the garment:
        pieces, seams, fabrics -- not the avatar) under %LOCALAPPDATA%\\md-mcp\\checkpoints
        (MD_MCP_CHECKPOINTS moves it), with a record of what the scene held; the newest
        30 are kept. Not a .zprj, because saving one re-points MD's open project at the
        copy, so the user's next Save would go over the checkpoint instead of their work
        (measured on 2026.0.315). Saving any copy clears MD's unsaved-changes flag; this
        sets it again, so MD still asks the user to save before closing.

        label: a few words to find it by later, e.g. "before lining seams".

        Returns path, label, scene (pieces, seam groups, avatars, fabrics), project (MD's
        open-project path, name and unsaved flag before and after), and recent
        checkpoints, with a warning if MD's project path or unsaved flag still changed.
        Fails, and says not to go on, if MD wrote no file.
        """
        path = checkpoints.new_path(label)
        saved, scene = bridge.checkpoint(str(path))
        if not saved.get("bytes"):
            raise bridge.BridgeError(
                f"No checkpoint was written: ExportZPac answered {saved.get('returned')!r} "
                f"and {path} is missing or empty. Do not make the change this was for "
                "until a checkpoint exists."
            )
        before = saved.get("project_before") or {}
        after = saved.get("project_after") or {}
        entry = checkpoints.record(
            path, label, scene, {"project_before": before, "project_after": after}
        )
        result: dict[str, Any] = {
            "path": str(path),
            "label": label,
            "bytes": saved["bytes"],
            "scene": entry["scene"],
            "project": {"before": before, "after": after},
        }
        warnings = []
        if "path" in before and "path" in after and before["path"] != after["path"]:
            warnings.append(
                f"Saving the checkpoint re-pointed MD's open project from "
                f"{before['path']!r} to {after['path']!r}. A Save in MD now writes over the "
                "checkpoint, not the original project -- tell the user before they save."
            )
        if before.get("unsaved") and not after.get("unsaved"):
            warnings.append(
                "MD no longer flags unsaved changes, so it would close without asking -- "
                "tell the user to save their project before closing MD."
            )
        if warnings:
            result["warning"] = " ".join(warnings)
        pruned = checkpoints.prune()
        if pruned:
            result["pruned"] = len(pruned)
        result["recent"] = [
            {key: item.get(key) for key in ("path", "label", "created")}
            for item in checkpoints.listed()[:5]
        ]
        return result

    @server.tool(annotations=REPLACES)
    @_reportable
    def md_restore(checkpoint: str = "latest") -> dict:
        """Put a checkpoint's garment back. DISCARDS THE GARMENT THAT IS OPEN NOW.

        checkpoint: "latest" (the default), a label given to md_checkpoint, a checkpoint
            file name, or a path to any .zpac or .zprj.

        A .zpac (what md_checkpoint saves) replaces the garment and keeps the avatar that
        is open. A .zprj replaces everything, avatar included, and re-points MD's open
        project at that file. Afterwards MD's unsaved-changes flag is set, since the
        scene no longer matches the user's saved file. Returns restored (the path),
        scene, matches_checkpoint and differences from the record kept when the
        checkpoint was saved (the avatar is not compared for a .zpac). Piece indices are
        the checkpoint's, not those of the garment that was replaced -- look pieces up by
        name again before the next call that takes an index.
        """
        entry = checkpoints.resolve(checkpoint)
        if entry is None:
            recent = [
                f"{item.get('label') or '(no label)'}: {item['path']}"
                for item in checkpoints.listed()[:10]
            ]
            raise ValueError(
                f"no checkpoint matches {checkpoint!r}. "
                + ("Recent ones: " + "; ".join(recent) if recent else
                   f"There are none in {checkpoints.folder()} yet.")
            )
        loaded, scene = bridge.restore(entry["path"])
        garment_only = not entry["path"].lower().endswith(".zprj")
        result: dict[str, Any] = {
            "restored": entry["path"],
            "label": entry.get("label", ""),
            "returned": loaded.get("returned"),
            "scene": scene,
            "project_after": loaded.get("project_after"),
        }
        if "scene" in entry:
            differences = checkpoints.compare(entry["scene"], scene, garment_only)
            result["matches_checkpoint"] = not differences
            result["differences"] = differences
        else:
            result["matches_checkpoint"] = None
            result["differences"] = ["no record was kept with this file, so nothing to compare"]
        return result

    # The last few ledgers md_seams read, by fingerprint, so a later call can say what
    # changed since one of them. In this process only: a restarted server starts empty.
    ledgers: dict[str, dict] = {}

    @server.tool(annotations=READS)
    @_reportable
    def md_seams(since: str = "", max_groups: int = 200) -> dict:
        """What MD has actually sewn to what: the seam ledger, read out of MD.

        MD's API only counts seam groups. This exports the pattern JSON inside MD and
        decodes every seam into its two sides: the piece, the span along its outline in
        mm, the outline lines that span covers (MD's line numbers, as
        AddSeamlinePairGroup takes them), the direction, and whether the span runs
        backwards. Read it after every sewing step: a sewing call accepts valid indices
        that join the wrong edges, and some MD operations sew on their own -- Layer
        Clone sews every edge of the clone to its original.

        since: a fingerprint from an earlier md_seams reply. The reply then lists the
            seams added and removed since that read -- which is how a seam nobody
            planned shows up. Only the last 20 ledgers this server read are kept.
        max_groups: how many groups to list (default 200); the rest are counted.

        Returns fingerprint, group_count, groups: [{index, name, turned, fold: {angle,
        strength}, pairs: [{a, b, length_mm}]}], and warnings by kind: "self" (an edge
        sewn to an overlapping part of itself), "reused" (one stretch of edge in two
        seams -- a lining sewn to its shell at the shoulder shows up here), "length" (two
        sides differing by more than 5 mm), "unknown_piece", "count" (MD's count and
        the export disagree) and "line_order" (MD's line lengths disagree with the
        document, so line numbers may be off). The scene is not changed.
        """
        if max_groups < 0:
            raise ValueError(f"max_groups cannot be negative, not {max_groups!r}")
        full = seams.ledger(bridge.seams(), max_groups=10**9)
        result = dict(full)
        result["groups"] = full["groups"][:max_groups]
        if len(full["groups"]) > max_groups:
            result["groups_omitted"] = len(full["groups"]) - max_groups
        if since:
            before = ledgers.get(since)
            if before is None:
                result["since"] = (
                    f"no ledger with fingerprint {since!r} was read by this server -- "
                    "only the last 20 are kept, and none survive a restart"
                )
            else:
                result.update(seams.diff(before, full))
        ledgers.pop(full["fingerprint"], None)
        ledgers[full["fingerprint"]] = full
        while len(ledgers) > 20:
            ledgers.pop(next(iter(ledgers)))
        return result

    def _pieces_by_name(found: dict) -> dict[str, dict]:
        """The board's pieces by name, with the detail md_sew resolves edges against."""
        named: dict[str, dict] = {}
        for piece in found["pieces"]:
            entry = named.setdefault(piece["name"], {"index": piece["index"], "count": 0})
            entry["count"] += 1
            if "input" in piece:
                read = drafting.parse_input_information(piece["input"])
                entry["points"], entry["lengths"] = read["points"], read["lengths"]
        return named

    @server.tool(annotations=BUILDS)
    @_reportable
    def md_draft_piece(
        name: str, outline: list[dict], at: list[float] | None = None,
        gap_mm: float = drafting.DEFAULT_GAP_MM,
    ) -> dict:
        """Draft one pattern piece from named edges, check MD built it, and remember them.

        Give the piece's corners in order. Each corner starts an edge that runs to the
        next corner (the last runs back to the first), and names it:
            {"at": [x, y], "edge": "hem"}
            {"at": [x, y], "edge": "armhole", "through": [[x, y], ...]}
        "through" lists the curve points of that edge; MD draws a smooth spline through
        them. Millimetres, x right, y up. Do not repeat the first corner at the end:
        MD closes the outline itself.

        Edge i is MD's line i, because an MD line runs from one corner to the next
        whatever curve points lie between. So any point sewn to two different
        counterparts -- a sleeve cap's top, a shoulder's end -- must be a corner.

        Refused before MD sees it: a repeated closing point, two points in one place, an
        outline that crosses itself, a duplicate edge name, a name already in the scene.

        at: where the piece's bottom-left goes on the 2D board. Default: gap_mm to the
            right of every piece already there, so nothing overlaps on the board.

        After creating the piece it reads MD's corners and line lengths back. If they
        are not the planned edges, the reply says so and no edge names are stored (the
        piece stays; delete it with DeletePatternPiece). Otherwise the edge names are
        kept in the garment's own metadata, where md_sew finds them.

        Returns index, name, edges [{name, line, start, end, curved,
        predicted_length_mm, md_length_mm}], bbox, area_mm2, winding, warnings,
        edges_stored. The piece is flat on the 2D board; nothing places it in 3D.
        """
        drafted = drafting.plan(name, outline)
        found = bridge.board()
        boxes = [
            {**drafting.board_box(piece["center"], piece["size"]), "name": piece["name"]}
            for piece in found["pieces"]
            if "center" in piece
        ]
        if any(piece["name"] == drafted["name"] for piece in found["pieces"]):
            raise ValueError(
                f"a piece named {drafted['name']!r} already exists. Names are how md_sew "
                "finds pieces, so they must be unique"
            )
        placed, warnings = drafting.lay_out(drafted, boxes, at=at, gap=gap_mm)

        made = bridge.draft(placed["name"], placed["points"], found["count"])
        if made.get("error"):
            raise bridge.BridgeError(made["error"])
        read_back = drafting.parse_input_information(made["input"])
        errors, length_warnings, md_lengths = drafting.verify(placed, read_back)
        warnings += length_warnings
        if made.get("name_read_back") != placed["name"]:
            errors.append(
                f"the piece was named {placed['name']!r} and MD reads it back as "
                f"{made.get('name_read_back')!r}"
            )

        edges = [
            {**edge, "md_length_mm": round(md_lengths[edge["line"]], 2)}
            if edge["line"] < len(md_lengths) else edge
            for edge in placed["edges"]
        ]
        result = {
            "index": made["index"],
            "name": placed["name"],
            "edges": edges,
            "bbox": placed["bbox"],
            "area_mm2": placed["area_mm2"],
            "winding": placed["winding"],
            "warnings": warnings,
        }
        if errors:
            result["errors"] = errors
            result["edges_stored"] = False
            result["next"] = (
                "MD did not build the planned edges, so edge names are not stored and "
                "md_sew cannot use them for this piece. Inspect it, then delete it with "
                f"pattern_api.DeletePatternPiece({made['index']}) or sew by line number."
            )
            return result

        store = drafting.load_store(found.get("store") or "")
        store["pieces"] = {
            key: value for key, value in store["pieces"].items()
            if key in {piece["name"] for piece in found["pieces"]}
        }
        store["pieces"][placed["name"]] = drafting.store_entry(placed, md_lengths)
        stored = bridge.store_edges(json.dumps(store, sort_keys=True))
        result["edges_stored"] = bool(stored.get("read_back_matches"))
        if not result["edges_stored"]:
            result["warnings"].append(
                "MD did not keep the edge names (the garment metadata read back "
                "without them), so md_sew needs line numbers for this piece"
            )
        return result

    @server.tool(annotations=BUILDS)
    @_reportable
    def md_sew(
        a: str, b: str, ends: str,
        max_difference_mm: float = sewing.DEFAULT_MAX_DIFFERENCE_MM,
        allow_layered: bool = False,
    ) -> dict:
        """Sew one edge to another by name, and confirm MD made exactly that seam.

        a, b: "piece.edge", where edge is a name from md_draft_piece or a line number
            ("front.side", "back.2").
        ends: which ends meet, and there is no default. "same": the start of a meets
            the start of b. "opposite": the start of a meets the end of b. An edge
            starts at the corner it was drafted from and ends at the next. The wrong
            choice builds a twisted seam that MD accepts and measures as correct.
        max_difference_mm: the most the two edges' lengths may differ (default 5, MD's
            own Check Sewing Length threshold). Raise it to sew deliberate ease.
        allow_layered: sew an edge that is already sewn. Off by default, because an
            edge sewn twice is usually a mistake rather than a lining.

        Refused before MD is touched: an edge sewn to itself (MD would stop on a modal
        dialog), an edge already sewn, a length difference over the limit, a stored
        edge map that no longer fits its piece. After sewing it reads the seam ledger
        back and checks that exactly one new seam joins exactly these two edges.

        Returns planned (both sides with their pieces, lines, lengths and spans, and the
        direction flags), returned (MD's bool), ok, added and problems. MD's API has no
        call that removes a seam: if ok is false, md_restore is the way back.
        """
        planned_refs = [sewing.parse_ref(a)[0], sewing.parse_ref(b)[0]]
        found = bridge.board(detail=planned_refs)
        pieces = _pieces_by_name(found)
        store = drafting.load_store(found.get("store") or "")
        side_a = sewing.resolve(a, pieces, store)
        side_b = sewing.resolve(b, pieces, store)
        before = seams.ledger(bridge.seams(), max_groups=10**9)
        planned = sewing.plan(
            side_a, side_b, ends, before,
            max_difference_mm=max_difference_mm, allow_layered=allow_layered,
        )

        sewn = bridge.sew(planned["args"], found["count"], found["seam_group_count"])
        if sewn.get("error"):
            raise bridge.BridgeError(sewn["error"])
        result: dict[str, Any] = {"planned": planned, "returned": sewn.get("returned")}
        for key in ("dialogs", "dialog_warning"):
            if sewn.get(key):
                result[key] = sewn[key]
        if sewn.get("returned") is False:
            result.update(ok=False, problems=[
                "AddSeamlinePairGroup returned False, which it does for an index that "
                "does not exist; nothing should have been made"])
            return result
        after = seams.ledger(bridge.seams(), max_groups=10**9)
        result.update(sewing.confirm(planned, before, after))
        result["fingerprint"] = after["fingerprint"]
        ledgers[after["fingerprint"]] = after
        return result

    def _read_meshes(avatar: bool = True, garment: bool = True):
        """MD's avatar and garment meshes, parsed, and its pieces; files cleaned up."""
        import shutil

        folder = tempfile.mkdtemp(prefix="md-mcp-meshes-")
        try:
            found = bridge.meshes(folder, avatar=avatar, garment=garment)
            files = found.get("files", {})
            if avatar and not files.get("avatar"):
                raise bridge.BridgeError("MD wrote no avatar OBJ; is an avatar loaded?")
            avatar_mesh = geometry.read_obj(files["avatar"]) if files.get("avatar") else None
            garment_mesh = (
                geometry.read_obj(files["garment"]) if files.get("garment") else None
            )
            if files.get("pattern"):
                with open(files["pattern"], encoding="utf-8") as handle:
                    found["outlines"] = fit.outline_lines(json.load(handle))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        return avatar_mesh, garment_mesh, found

    @server.tool(annotations=READS)
    @_reportable
    def md_body(heights: list[float] | None = None) -> dict:
        """The avatar's measurements, found from its mesh: what to draft a pattern to.

        Landmark heights -- crotch, armpit, waist, hip, bust -- and the torso's girth at
        each, read from exact horizontal sections of the avatar MD exports. A girth is
        what a tape measure reads: the perimeter of the section's convex hull, so the
        hollow between the breasts is bridged as a tape bridges it. The waist is the
        narrowest torso section between crotch and armpit, the hip the widest below it,
        the bust the widest above it.

        heights: extra heights in mm to section at. Each lists every closed loop there
            -- torso, arms, legs -- with its girth, x and z range and centre.

        Millimetres, Y up, +Z the avatar's front, +X the avatar's left. The scene is not
        changed. Expects a standing avatar centred on x = 0, as MD's are.
        """
        avatar, _, found = _read_meshes(avatar=True, garment=False)
        result: dict[str, Any] = {"avatars": found.get("avatars", [])}
        result.update(geometry.body_landmarks(avatar))
        if heights:
            slicer = geometry.Slicer(avatar)
            sections = []
            for h in heights:
                loops = [loop for loop in slicer.loops(float(h)) if loop.closed]
                sections.append({"height": h, "loops": [
                    {"girth": round(loop.girth, 1),
                     "x": [round(v, 1) for v in loop.x_range],
                     "z": [round(v, 1) for v in loop.z_range],
                     "centre": [round(v, 1) for v in loop.centre]}
                    for loop in sorted(loops, key=lambda loop: loop.centre[0])]})
            result["sections"] = sections
        return result

    @server.tool(annotations=READS)
    @_reportable
    def md_fit(
        rings: dict[str, list[str]] | None = None, draped: bool = False, body: bool = True,
        gaps: dict[str, list[str]] | None = None,
    ) -> dict:
        """Check the garment in 3D against its seams and the body -- before or after a drape.

        Seams: every seam in MD's ledger, each side found in 3D and compared point for
        point in the order the seam joins them. Before simulating, the gap is expected;
        "paths_through_cloth" and "paths_through_body" count straight paths between
        partners that run through a panel or the avatar -- edges that may start crossed.
        Through the torso, alongside cloth inside the body, that caught a skirt side
        seam open 100 mm; round an arm it is common and T-shirt side seams closed anyway
        (both measured 2026-10-12). It also flags partners that would sit far closer
        paired the other way round: wrong ends, or a piece arranged mirrored. After simulating, "open_mm" is seam length whose
        partners are still more than 10 mm apart: a seam that did not close.

        Pieces: each piece's height range, centroid, vertices inside the body and how
        deep (and where), mesh edges the body passes through, and clearance from the
        body (median and spread, mm). The edge test catches what the vertex test cannot:
        trousers with straight legs on an A-posed avatar showed 1-2 vertices inside,
        while the shins had left the trouser legs below the knee -- 8 edges per panel
        crossed the body there (measured 2026-10-12). Contact under 2 mm is noise.

        rings: openings and lines that go round the body, by name: {"waist":
            ["front.waist", "back.waist"]}. Each reports its 3D length, the height it
            sits at, the body's torso girth there, and the ease -- length minus girth.
            Negative ease is cloth stretched over the body.
        draped: say True after simulating. Then seam partners still more than 10 mm
            apart are a problem -- the seam did not close; before a drape they are
            expected and only the paths between them matter.
        body: compare with the avatar (default). False skips the body tests.
        gaps: pairs of edges left unsewn, by name: {"front": ["front_L.cf",
            "front_R.cf"]}. Each reports how far apart they hang, overall and top to
            bottom -- how open an open front is, measured rather than judged by eye.

        Each piece also reports pattern_axes: which way its 2D +x and +y run on the body
        and whether its drafted face looks away from it -- measured, which is how to
        tell a seam's ends from the pieces as they sit.

        Returns seams, pieces, rings, pieces_crossing, problems and warnings. Problems:
        cloth inside the body, pieces passing through each other, seams still open after
        a drape, a piece whose edges could not be located.
        Warnings are risks to look at: a T-shirt whose side-seam paths crossed an arm
        still closed in the drape (measured), while a skirt with 513 vertices starting
        inside the hips caught a seam open. The scene is not changed.
        """
        avatar, garment, found = _read_meshes(avatar=body, garment=True)
        if garment is None:
            raise ValueError("there is no garment to check: MD exported no pattern pieces")
        shape = {}
        pieces_named = {}
        outlines = found.get("outlines", {})
        unregistered = []
        for piece in found["pieces"]:
            read = drafting.parse_input_information(piece["input"])
            shape[piece["name"]] = {"index": piece["index"], "count": 1,
                                    "points": read["points"], "lengths": read["lengths"]}
            lines = outlines.get(piece["name"])
            if not lines or len(lines) != len(read["lengths"]):
                unregistered.append(piece["name"])
                continue
            try:
                pieces_named[piece["name"]] = fit.PieceMap(
                    garment, piece["name"], lines, read["lengths"])
            except KeyError:
                continue
        body_model = geometry.Body(avatar) if avatar is not None else None
        body_index = (
            geometry.TriangleIndex(avatar.vertices, avatar.triangles())
            if avatar is not None else None
        )
        garment_index = geometry.TriangleIndex(garment.vertices, garment.triangles())
        ledger = seams.ledger(bridge.seams(), max_groups=10**9)

        store = drafting.load_store(bridge.board().get("store") or "")
        # Edge names label the report only where the stored map still has the piece's
        # line count; a reshaped piece is reported by line number.
        names = {
            piece: {line: edge for edge, line in entry.get("edges", {}).items()}
            for piece, entry in store["pieces"].items()
            if piece in shape and len(entry.get("lengths_mm", [])) == len(shape[piece]["lengths"])
        }
        result: dict[str, Any] = {
            "seams": fit.seam_report(pieces_named, ledger, garment_index, body_index,
                                     garment.vertices, names),
            "pieces": {name: fit.piece_report(p, body_model, body_index)
                       for name, p in pieces_named.items()},
        }
        ring_reports = {}
        for ring_name, refs in (rings or {}).items():
            edges = []
            for ref in refs:
                side = sewing.resolve(ref, shape, store)
                edges.append((side["piece"], side["line"]))
            ring_reports[ring_name] = fit.ring_report(ring_name, edges, pieces_named,
                                                      body_model, names)
        result["rings"] = ring_reports
        gap_reports = {}
        for gap_name, refs in (gaps or {}).items():
            if len(refs) != 2:
                raise ValueError(f"gap {gap_name!r} needs exactly two edges, got {len(refs)}")
            sides = [sewing.resolve(ref, shape, store) for ref in refs]
            for side in sides:
                if side["piece"] not in pieces_named:
                    raise ValueError(f"gap {gap_name!r}: {side['piece']!r} was not found in 3D")
            gap_reports[gap_name] = fit.gap_report(
                (sides[0]["piece"], sides[0]["line"]), (sides[1]["piece"], sides[1]["line"]),
                pieces_named, names)
        if gap_reports:
            result["gaps"] = gap_reports
        warnings = [f"{s['group']} ({s['seam']}): {w}"
                    for s in result["seams"] for w in s.get("warnings", [])]
        for ring_name, ring in ring_reports.items():
            if draped and (ring.get("stretch_pct") or 0) > fit.RING_STRETCH_PCT:
                warnings.append(
                    f"ring {ring_name}: {ring['stretch_pct']} % longer than drafted -- it is "
                    "carrying the garment, squeezed round the body, or a knit hanging by "
                    "its own weight. Measured 2026-10-12: an armhole with no ease over the "
                    "arm root (12 %, the arm through the sleeve) and plain waists that slid "
                    "onto the hips (10-17 %) -- draft longer, or hold a waist with elastic; "
                    "jersey armholes with 100 mm of ease, 5-8 %")
            if ring.get("drafted_ease_mm", 0) < 0:
                warnings.append(
                    f"ring {ring_name}: drafted {ring['drafted_mm']} mm round "
                    f"{ring['body_girth_mm']} mm of body in its own plane, so it can only "
                    "sit there stretched or move until it fits -- intended for an "
                    "elastic waist, otherwise draft it longer")
        problems = [
            f"{name}: not checked -- its outline in MD's pattern document does not match "
            "its lines (a duplicate name, or an export without it)" for name in unregistered]
        for name, p in result["pieces"].items():
            if p.get("registration_residual_mm", 0) > 2.0:
                problems.append(
                    f"{name}: its corners fit its 3D outline only to "
                    f"{p['registration_residual_mm']} mm, so its edges may be misplaced "
                    "in this report")
        for s in result["seams"] if draped else []:
            if s.get("open_mm", 0) > 0:
                problems.append(f"{s['group']} ({s['seam']}): {s['open_mm']} mm of seam has partners "
                                "more than 10 mm apart after the drape: it did not close")
        result["pieces_crossing"] = fit.pieces_crossing(garment, sorted(pieces_named))
        for crossing in result["pieces_crossing"]:
            a, b = crossing["pieces"]
            problems.append(
                f"{a} and {b} pass through each other -- {crossing['edges']} mesh edges, "
                f"heights {crossing['y'][0]} to {crossing['y'][1]} mm: the solver pushes "
                "one out, and an arranged pair has been measured flung round to the side "
                "seam. Move one with md_place's around or clearance_mm before simulating")
        for name, p in result["pieces"].items():
            if p.get("edges_through_body"):
                problems.append(
                    f"{name}: the body passes through the cloth -- {p['edges_through_body']}"
                    f" mesh edges cross its surface, between heights "
                    f"{p['through_body_y'][0]} and {p['through_body_y'][1]} mm")
            elif p.get("deepest_inside_mm", 0) > fit.INSIDE_TOLERANCE_MM:
                problems.append(f"{name}: {p['inside_body']} vertices inside the body, up "
                                f"to {p['deepest_inside_mm']} mm deep")
        result["problems"] = problems
        result["warnings"] = warnings
        return result

    @server.tool(annotations=TOUCHES)
    @_reportable
    def md_place(
        piece: str,
        point: str,
        height_mm: float | None = None,
        anchor: str = "center",
        clearance_mm: float = 20.0,
        around: int | None = None,
        style: str = "Flat",
        orientation: int | None = None,
    ) -> dict:
        """Arrange a piece on the body and steer it until it sits where it is meant to.

        MD has no call that moves a piece freely in 3D. What it has is an arrangement
        point, which receives the centre of the piece's 2D box, and three values on the
        point's bounding volume -- round it (x), along it (y), out from it (offset),
        0-100 each. Measured on Genesis 8 with MD's Adult V2 points (2026-10-12): y ran
        a piece's centre from 888 to 1467 mm, offset 2 mm a unit out from the body, and
        x round it (50 the front centre, 25 the avatar's left, 0 and 100 the back). The
        units are the volume's, so this does not predict: it sets values, exports the
        piece, measures where it landed, and corrects -- a bounded integer search, the
        same answer every time for the same scene.

        piece: the piece's name.
        point: an arrangement point, its exact name or a part of one that matches only
            one ("Body_Front_Center_5"). It chooses the bounding volume and the defaults.
            Points' names are not positions -- md_arrangement_points shows where they
            land.
        height_mm: where the anchor should sit, in mm above the floor. Omit to keep the
            point's own height.
        anchor: "center" (the piece's middle) or one of its edges, by name or line
            number -- "waist" puts the waist edge's average height at height_mm.
        clearance_mm: the least distance between the piece and the body (default 20).
            The offset is raised until the nearest vertex is at least this far out, and
            no further than needed.
        around: the x value, 0-100, overriding the point's own -- to move a piece round
            the body. Omit to keep the point's.
        style: "Flat" (default) or "Curved". Curved wraps the piece round the volume;
            measured on a skirt, it started the side edges inside the hips and crossed,
            and the side seam caught open in the drape. Flat pieces arrive as planes,
            which the seams then pull round the body.
        orientation: degrees; default the point's own (180 on every measured point).

        Pieces already placed with md_place are kept clear of: the offset also rises until
        this piece passes through none of them (through_pieces names any it still does).

        Returns the point, the values chosen, the anchor height and clearance achieved,
        vertices inside the body, the piece's pattern axes in 3D, and every try. Check
        the whole garment with md_fit before simulating.
        """
        if style not in ("Flat", "Curved"):
            raise ValueError("style must be 'Flat' or 'Curved', the two MD accepts")
        listed = bridge.arrangement_list()
        names = [p.get("ArrangementName", "") for p in listed]
        hits = [i for i, n in enumerate(names) if n == point] or [
            i for i, n in enumerate(names) if point.lower() in n.lower()]
        if not names:
            raise ValueError(
                "the avatar has no arrangement points. MD ships them: import_api.ImportFile "
                "the Adult_V2_Arrangement_Bounding_Volume.pan and then "
                "Adult_V2_Arrangement_Point.arr from Preset/Avatar/Arrangement -- measured "
                "to give Genesis 8 109 points")
        if len(hits) != 1:
            raise ValueError(
                f"{point!r} matches {len(hits)} arrangement points"
                + (f": {[names[i] for i in hits][:12]}" if hits else
                   f"; some are {names[:12]}"))
        entry = listed[hits[0]]

        def value(key: str, default: int) -> int:
            try:
                return int(float(entry.get(key, default)))
            except (TypeError, ValueError):
                return default

        x = around if around is not None else value("ArrangementOffsetX", 50)
        y = value("ArrangementOffsetY", 50)
        offset = value("ArrangementOffsetZ", 50)
        turn = orientation if orientation is not None else value("ArrangementOrientation", 180)
        for label, number in (("around", x),):
            if not 0 <= number <= 100:
                raise ValueError(f"{label} must be 0-100, not {number}")

        found = bridge.board(detail=[piece])
        pieces = _pieces_by_name(found)
        if piece not in pieces:
            raise ValueError(f"no piece is named {piece!r}")
        if pieces[piece]["count"] > 1:
            raise ValueError(f"{pieces[piece]['count']} pieces are named {piece!r}")
        index = pieces[piece]["index"]
        store = drafting.load_store(found.get("store") or "")
        anchor_line = None
        if anchor != "center":
            anchor_line = sewing.resolve(f"{piece}.{anchor}", pieces, store)["line"]
        # Pieces md_place has already put on the body: this one must not pass through
        # them. Pieces not yet arranged sit wherever MD drafted them and are ignored.
        placed = sorted(name for name, entry in store["pieces"].items()
                        if entry.get("placed") and name != piece and name in pieces)

        avatar, _, _ = _read_meshes(avatar=True, garment=False)
        body_model = geometry.Body(avatar)
        tries: list[dict] = []

        def measure(values: list[int]) -> dict:
            bridge.arrange(index, piece, turn, values=values)
            _, garment, meta = _read_meshes(avatar=False, garment=True)
            info = next(p for p in meta["pieces"] if p["name"] == piece)
            read = drafting.parse_input_information(info["input"])
            mapped = fit.PieceMap(garment, piece, meta["outlines"][piece], read["lengths"])
            pts = [mapped.piece.vertices[v] for v in mapped.vertex_ids()]
            if anchor_line is None:
                ys = [p[1] for p in pts]
                height = (min(ys) + max(ys)) / 2.0
            else:
                line = mapped.line_points(anchor_line)
                height = sum(p[1] for p in line) / len(line)
            clearances = [c for c in (body_model.clearance(p) for p in pts) if c is not None]
            crossing = {}
            for crossed in fit.pieces_crossing(garment, [piece] + placed):
                if piece in crossed["pieces"]:
                    other = next(n for n in crossed["pieces"] if n != piece)
                    crossing[other] = crossed["edges"]
            state = {
                "values": list(values),
                "anchor_height_mm": round(height, 1),
                "min_clearance_mm": round(min(clearances), 1) if clearances else None,
                "inside_body": sum(1 for c in clearances if c < 0),
                "through_pieces": crossing,
                "axes": fit.pattern_axes(mapped),
            }
            tries.append({k: v for k, v in state.items() if k != "axes"})
            return state

        bridge.arrange(index, piece, turn, point=hits[0], style=style, values=[x, y, offset])
        state = measure([x, y, offset])

        def solve_height(state: dict) -> dict:
            nonlocal y
            if height_mm is None:
                return state
            low, high = measure([x, 0, offset]), measure([x, 100, offset])
            span = high["anchor_height_mm"] - low["anchor_height_mm"]
            if abs(span) < 1.0:
                return state
            guess = round((height_mm - low["anchor_height_mm"]) / span * 100)
            y = max(0, min(100, guess))
            state = measure([x, y, offset])
            for _ in range(3):
                error = height_mm - state["anchor_height_mm"]
                step = round(error / span * 100)
                if step == 0 or not 0 <= y + step <= 100:
                    break
                y += step
                state = measure([x, y, offset])
            return state

        state = solve_height(state)

        # The least offset whose nearest vertex clears the body by clearance_mm and whose
        # cloth passes through no piece already placed -- two fronts with extensions
        # arranged through each other end up layered, the second outside the first.
        def clear_enough(s: dict) -> bool:
            return (s["inside_body"] == 0 and (s["min_clearance_mm"] or 0) >= clearance_mm
                    and not s["through_pieces"])

        if not clear_enough(state):
            low, high = offset, 100
            top = measure([x, y, high])
            if clear_enough(top):
                while high - low > 1:
                    middle = (low + high) // 2
                    if clear_enough(measure([x, y, middle])):
                        high = middle
                    else:
                        low = middle
                offset = high
            else:
                offset = high
        else:
            low, high = 0, offset
            while high - low > 1:
                middle = (low + high) // 2
                if clear_enough(measure([x, y, middle])):
                    high = middle
                else:
                    low = middle
            offset = high
        state = measure([x, y, offset])
        if height_mm is not None and abs(height_mm - state["anchor_height_mm"]) > 6:
            state = solve_height(state)
            state = measure([x, y, offset])

        result: dict[str, Any] = {
            "piece": piece,
            "point": names[hits[0]],
            "style": style,
            "orientation": turn,
            "values": {"around": x, "along": y, "offset": offset},
            "anchor": anchor,
            "anchor_height_mm": state["anchor_height_mm"],
            "min_clearance_mm": state["min_clearance_mm"],
            "inside_body": state["inside_body"],
            "through_pieces": state["through_pieces"],
            "pattern_axes": state["axes"],
            "tries": tries,
        }
        problems = []
        if height_mm is not None and abs(height_mm - state["anchor_height_mm"]) > 6:
            problems.append(
                f"the anchor sits at {state['anchor_height_mm']} mm, not {height_mm}: the "
                "point's volume does not reach that height -- try a point nearer it")
        if state["through_pieces"]:
            problems.append(
                f"the piece still passes through {sorted(state['through_pieces'])} at the "
                "most offset: move it round the body with around, or place the other first")
        elif not clear_enough(state):
            problems.append(
                f"the piece clears the body by {state['min_clearance_mm']} mm at most "
                f"offset, with {state['inside_body']} vertices inside: try another point "
                "or a smaller piece")
        if piece in store["pieces"]:
            store["pieces"][piece]["placed"] = names[hits[0]]
            bridge.store_edges(json.dumps(store, sort_keys=True))
        result["problems"] = problems
        return result

    @server.tool(annotations=READS)
    @_reportable
    def md_edges(piece: str = "") -> dict:
        """The edge names md_draft_piece stored, checked against the pieces as they are.

        piece: one piece's name; empty lists every piece with stored names.

        Returns pieces: {name: {edges: {edge: line}, lengths_mm (MD's, now), valid, why}}.
        A piece whose shape changed since it was drafted is marked not valid -- its line
        numbers may have moved -- and md_sew refuses its edge names until it is drafted
        again. The scene is not changed.
        """
        found = bridge.board()
        store = drafting.load_store(found.get("store") or "")
        wanted = [piece] if piece else sorted(store["pieces"])
        detail = bridge.board(detail=wanted) if wanted else found
        current = _pieces_by_name(detail)
        listed = {}
        for name in wanted:
            entry = store["pieces"].get(name)
            if entry is None:
                listed[name] = {"valid": False, "why": "no edge names are stored for it"}
                continue
            now = current.get(name)
            if now is None or "points" not in now:
                listed[name] = {"edges": entry["edges"], "valid": False,
                                "why": "no piece of that name is in the scene"}
                continue
            stale = drafting.still_matches(entry, now["points"])
            listed[name] = {
                "edges": entry["edges"],
                "lengths_mm": [round(length, 2) for length in now["lengths"]],
                "valid": stale is None and now["count"] == 1,
                "why": stale or (None if now["count"] == 1 else
                                 f"{now['count']} pieces share the name"),
            }
        return {"pieces": listed}

    return server


def main() -> None:
    """Console-script entry point: serve over stdio."""
    build_server().run("stdio")


if __name__ == "__main__":
    main()
