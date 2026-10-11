"""The MCP server: Marvelous Designer's whole API, over the live bridge.

Four tools reach the API. ``md_status`` says whether MD is reachable, ``md_api``
searches everything the running build exposes, ``md_call`` calls any of it, and
``run_md_python`` runs a script inside MD for work that is several calls deep. There is
no curated subset and no wrapper per feature: the API MD ships is the API this serves.

Four more read back what that API cannot: ``md_measure`` (3D extents, from OBJ exports),
``md_arrangement_points`` (where arrangement points really are, found with a probe
piece), ``md_snapshot`` (a picture of the 3D window, returned as an image) and
``md_fabric_presets`` (the ``.zfab`` files ``AddFabric`` needs). They add no calls to
MD; each is a script run through the same bridge.

Everything here is a thin shell. The socket is in ``md_mcp.bridge``; the half that runs
inside MD is ``plugin/md_mcp_listener.py``.

Tools **fail** rather than returning an empty result when MD is not listening. A caller
that read "no patterns found" as success would conclude MD is connected and idle, which
is the opposite of true. See ``docs/adr/001-bridge-transport.md``.
"""

from __future__ import annotations

import functools
import os
import pathlib
import struct
import sys
import tempfile
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from md_mcp import __version__, bridge


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


def build_server() -> MCPServer:
    server = MCPServer(
        name="md-mcp",
        version=__version__,
        instructions=(
            "Drives a running Marvelous Designer. md_api searches everything this "
            "build of MD exposes, md_call calls any of it, run_md_python runs a whole "
            "script inside MD. Search before calling: the search gives the exact "
            "spelling and arity, and MD dispatches its overloads on arity. Lengths are "
            "millimetres and there is no undo. MD must be open with the bridge "
            "started (Plugin tab > Start MCP).\n"
            "MD fails quietly more often than it fails loudly, so read the scene back "
            "rather than trusting a return value. Three that were measured on "
            "2026.0.315: utility_api.Simulate(n) runs n solver steps and returns True "
            "for any n, so Simulate(1) leaves the garment flat -- it needs a few "
            "hundred, and repeated calls continue from where the last left off. A "
            "sewing call naming a missing line or piece returns False; valid indices "
            "can still pair the wrong edges, so check seam_group_count and the scene. "
            "And when a surface will not take a fabric, check it is a pattern piece "
            "before blaming the binding: measured 2026-09-19, setting all four fabrics "
            "to probe colours and then every one of 56 pieces to a single fabric left "
            "some strap-shaped geometry unchanged, because it was not a pattern piece "
            "at all. Recolour the fabric and look; the reported index is not evidence "
            "either way. Before sewing, check a ledger of existing and planned "
            "edge pairs: avoid duplicate pairs, self-sewing and unintended reuse of "
            "edge intervals. Verify lengths, intended ease and endpoint direction; "
            "counts alone cannot prove correct seams or absence of 3D intersections.\n"
            "The call API cannot see the result in 3D, so four tools read it back: "
            "md_measure (avatar and garment bounds, avatar slices at given heights), "
            "md_arrangement_points (where arrangement points really are -- their names "
            "can be a metre out), md_snapshot (a picture of the 3D window) and "
            "md_fabric_presets (the .zfab paths AddFabric needs). Exports are checked "
            "for the files they claim: missing_outputs means MD reported a file it did "
            "not write."
        ),
    )

    @server.tool()
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
        return status

    @server.tool()
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

    @server.tool()
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
        result, writes = bridge.call_checked(function, args or [])
        reply = {"function": function, "result": result}
        reply.update(_write_fields(writes, function))
        return reply

    @server.tool()
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
        return result

    @server.tool()
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

    @server.tool()
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

    @server.tool()
    @_reportable
    def md_snapshot(
        view: int | None = None, max_size: int | None = SNAPSHOT_MAX_SIZE
    ) -> list[Image | dict]:
        """Look at MD's 3D window: a snapshot, returned as an image.

        view: a camera position, 0 to 9, set with SetCamViewPoint. Measured on
            2026.0.315: 0 bottom, 1 three-quarter right, 2 front, 3 three-quarter left,
            4 right, 5 top, 6 left, 8 back; 7 zooms in and 9 zooms to fit, both keeping
            the direction the camera already had. Omit it to photograph the camera where
            it is. (SetViewPoint, one word away, is accepted and moves nothing.)
        max_size: the longest edge in pixels, default 1024, so the image stays cheap to
            keep in the conversation. 0 sends it at full size.

        THE CAMERA IS LEFT WHERE THIS PUT IT. MD has no call that reads the camera back
        (GetViewPoint answers -1 whatever is set), so the previous view cannot be
        restored. Comparing snapshots by hash proves nothing either way -- a draped
        scene never gives the same bytes twice -- so compare the pictures.
        """
        if view is not None and view not in CAMERA_VIEWS:
            raise ValueError(
                f"view must be 0 to 9 -- the ten SetCamViewPoint positions -- or "
                f"omitted, not {view!r}"
            )
        if max_size is not None and max_size < 0:
            raise ValueError(f"max_size cannot be negative, not {max_size!r}")
        data, shot = bridge.snapshot(view)
        kind = _image_format(data)
        if kind is None:
            raise bridge.BridgeError(
                "MD's snapshot is neither a PNG nor a JPEG; its first bytes were "
                f"{data[:8]!r}"
            )
        meta: dict[str, Any] = {
            "view": view,
            "facing": CAMERA_VIEWS.get(view, "wherever the camera already was"),
            "camera": (
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
        return [meta, Image(data=data, format=kind)]

    @server.tool()
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

    return server


def main() -> None:
    """Console-script entry point: serve over stdio."""
    build_server().run("stdio")


if __name__ == "__main__":
    main()
