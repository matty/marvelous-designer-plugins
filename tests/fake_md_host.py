"""A stand-in for Marvelous Designer's host modules, so the listener can be tested.

``plugin/md_mcp_listener.py`` is the half of the bridge that runs inside MD, and it is
the half that used to be untestable: it imports ``pattern_api`` and ``utility_api``,
which only exist in MD's embedded interpreter. Injecting fakes under those names lets
the real listener file run in-process here -- the same source that ships, not a copy of
its logic -- so the protocol, the exec namespace and the error paths are all covered
without MD. What the fakes cannot prove is that MD itself accepts the calls; that is
what ``tests/md_required/test_bridge_live.py`` is for.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import pathlib
import socket
import struct
import sys
import types
import zlib
from typing import Iterator

LISTENER = pathlib.Path(__file__).resolve().parents[1] / "plugin" / "md_mcp_listener.py"
STOPPER = LISTENER.with_name("md_mcp_stop.py")


def png_bytes(width: int, height: int, rgb: tuple[int, int, int] = (200, 80, 40)) -> bytes:
    """A real, minimal PNG of one colour -- what a snapshot is, without MD to take one."""

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class ImportExportOption:
    """ApiTypes.ImportExportOption, for the two fields the geometry tools set.

    Slotted so that a misspelt field raises, as assigning an unknown attribute to the
    pybind struct in MD would, rather than being silently kept.
    """

    __slots__ = ("bExportAvatar", "bExportGarment")

    def __init__(self) -> None:
        self.bExportAvatar = True
        self.bExportGarment = True


class FakeMDHost:
    """Records the calls an emitted script makes, and answers the readback calls."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.piece_names: list[str] = []
        self.shape_counts: list[int] = []
        self.seam_groups = 0
        #: Seams sewn with AddSeamlinePairGroup's six arguments, which the pattern JSON
        #: export writes out -- (piece A, line A, piece B, line B, flag A, flag B).
        self.seams: list[tuple] = []
        self.avatars: list[str] = []
        self.fabrics: list[str] = ["FABRIC 1"]
        #: (name, path) for every RegisterPythonScript call. MD's own list cannot be
        #: read back, so this is the only place the listener's registration can be
        #: counted -- and counting it is the point: it must ask once per install.
        self.registered: list[tuple[str, str]] = []
        #: Set to raise from RegisterPythonScript, to prove that a refusal from MD
        #: costs a button and not a bridge.
        self.registration_error: Exception | None = None
        #: MD's call answers a bool. Set False for the other kind of refusal -- the one
        #: that registers nothing and raises nothing. Measured on 2026.0.315: MD does
        #: that for an invalid path and for a path it already has.
        self.registration_result: bool = True
        #: Every DisplayMessageBox. Start MCP and Stop MCP report through one, because
        #: where a clicked plugin's stdout goes has not been measured.
        self.message_boxes: list[str] = []

        # -- 3D: what the OBJ and snapshot exports are made from ------------------
        #: Each piece's outline as given, and where it was arranged (None: flat, at
        #: its board coordinates). An arranged piece is centred on the point.
        self.piece_points: list[list[tuple[float, float, float]]] = []
        self.placements: list[tuple[float, float, float] | None] = []
        #: The avatar's vertices in mm, Y up and +Z its front, as MD's OBJ was measured.
        self.avatar_vertices: list[tuple[float, float, float]] = []
        #: (name, position) per arrangement point. Real positions are what md-mcp has to
        #: discover; here they are what a probe arranged there will be centred on.
        self.arrangement_points: list[tuple[str, tuple[float, float, float]]] = []
        #: How GetArrangementList spells an entry. MD's exact shape is unmeasured -- it
        #: carries names and percentage offsets -- so the tools must read both.
        self.arrangement_list_format = "dict"
        #: How ExportOBJ names a piece's group: "piece" (its name, as measured -- 'g
        #: front', 'g back'), "generic" (Pattern_<index>), or "none" (no groups at all).
        self.obj_groups = "piece"
        #: False makes every export do what ExportOBJ was measured doing into a folder
        #: that did not exist: return its path list and write nothing.
        self.exports_write = True
        self.snapshot_size = (640, 480)
        self.camera: int | None = None
        #: Every file an export actually wrote, so a test can prove they were removed.
        self.written: list[str] = []

    # ------------------------------------------------------------ pattern_api

    def CreatePatternWithPoints(self, points) -> int:
        self.calls.append(("CreatePatternWithPoints", len(points)))
        self.piece_names.append(f"pattern-{len(self.piece_names)}")
        self.shape_counts.append(0)
        self.piece_points.append([tuple(float(c) for c in point) for point in points])
        self.placements.append(None)
        return len(self.piece_names) - 1

    def DeletePatternPiece(self, index: int) -> None:
        # Renumbers every later piece, as MD's does.
        self.calls.append(("DeletePatternPiece", index))
        for column in (self.piece_names, self.shape_counts, self.piece_points,
                       self.placements):
            del column[index]

    def GetArrangementList(self) -> list:
        if self.arrangement_list_format == "str":
            return [name for name, _ in self.arrangement_points]
        return [
            {"ArrangementName": name, "ArrangementOffsetX": "50",
             "ArrangementOffsetY": "50"}
            for name, _ in self.arrangement_points
        ]

    def SetArrangement(self, piece: int, point: int) -> bool:
        self.calls.append(("SetArrangement", piece, point))
        if not 0 <= point < len(self.arrangement_points):
            return False
        self.placements[piece] = self.arrangement_points[point][1]
        return True

    def _line_lengths(self, index: int) -> list[float]:
        points = self.piece_points[index]
        return [
            math.dist(points[k][:2], points[(k + 1) % len(points)][:2])
            for k in range(len(points))
        ]

    def GetLineLength(self, index: int, line: int) -> float:
        return self._line_lengths(index)[line]

    def ExportPatternJSON(self, path: str) -> None:
        """A pattern document in MD's shape (see tests/fixtures/cape_back_and_front.json).

        Each piece's outline is one line per edge, sharing end points; each recorded
        seam is a pair of spans as fractions of the outline's length, backwards on a
        side whose direction flag is False -- as MD's own export was measured.
        """
        self.calls.append(("ExportPatternJSON", path))
        patterns = []
        for index, points in enumerate(self.piece_points):
            lines = [
                {"ID": f"L{index}-{k}", "PointList": [
                    {"ID": f"P{index}-{k}-{e}", "PointType": "Straight",
                     "Position": {"x": point[0], "y": point[1]}}
                    for e, point in enumerate((points[k], points[(k + 1) % len(points)]))
                ]}
                for k in range(len(points))
            ]
            patterns.append({"ID": f"S{index}", "Name": self.piece_names[index],
                             "ShapeInfo": {"IsSlashed": False, "LineList": lines}})

        def side(piece: int, line: int, forward: bool) -> dict:
            lengths = self._line_lengths(piece)
            total = sum(lengths)
            start, end = sum(lengths[:line]) / total, sum(lengths[: line + 1]) / total
            if not forward:
                start, end = end, start
            return {"ShapeID": f"S{piece}", "LengthParam": {"fStart": start, "fEnd": end},
                    "Direction": forward}

        groups = [
            {"Name": f"Sewing_{n}", "bIsTurned": False,
             "FoldData": {"iAngle": 180, "iStrength": 5},
             "PairList": [{"First": side(a, la, da), "Second": side(b, lb, db)}]}
            for n, (a, la, b, lb, da, db) in enumerate(self.seams)
        ]
        document = {"formatVersion": "fake", "Unit": "mm", "PatternList": patterns,
                    "SeamLinePairGroupList": groups}
        self._write(path, json.dumps(document).encode("utf-8"))

    def ExportObjectBrowserMaterialsList(self) -> str:
        return "Fabrics\n1:FABRIC 1\nTrims\n1:Default Button\n"

    def piece_vertices(self, index: int) -> list[tuple[float, float, float]]:
        points = self.piece_points[index]
        placed = self.placements[index]
        if placed is None:
            return list(points)
        centre_x = (min(p[0] for p in points) + max(p[0] for p in points)) / 2
        centre_y = (min(p[1] for p in points) + max(p[1] for p in points)) / 2
        return [
            (placed[0] + x - centre_x, placed[1] + y - centre_y, placed[2])
            for x, y, _ in points
        ]

    # ------------------------------------------------------------- export_api

    def _write(self, path: str, data: bytes) -> bool:
        if not self.exports_write or not os.path.isdir(os.path.dirname(path)):
            return False
        with open(path, "wb") as handle:
            handle.write(data)
        self.written.append(path)
        return True

    def ExportOBJ(self, path: str, option: ImportExportOption | None = None) -> list:
        if option is None:
            # Measured: the bare-path overload gave no reply in 300s and wrote nothing.
            # Here it refuses instead, so a test proves the option is always passed.
            raise TypeError("fake MD: ExportOBJ(path) hangs in MD; pass an option")
        self.calls.append(("ExportOBJ", path, option.bExportAvatar, option.bExportGarment))
        lines = ["# fake Marvelous Designer", "mtllib fake.mtl"]
        count = 0

        def block(group: str | None, vertices, style: str) -> None:
            nonlocal count
            if group is not None:
                lines.append(f"g {group}")
            lines.extend("v {} {} {}".format(*vertex) for vertex in vertices)
            first = count + 1
            count += len(vertices)
            for k in range(1, len(vertices) - 1):
                corners = (first, first + k, first + k + 1)
                if style == "normals":
                    lines.append("f " + " ".join(f"{c}//{c}" for c in corners))
                else:
                    lines.append("f " + " ".join(str(c) for c in corners))

        if option.bExportAvatar and self.avatars and self.avatar_vertices:
            block("Mesh", self.avatar_vertices, "normals")
        if option.bExportGarment:
            for index, name in enumerate(self.piece_names):
                group = {"piece": name, "generic": f"Pattern_{index}", "none": None}[
                    self.obj_groups
                ]
                block(group, self.piece_vertices(index), "plain")
        material = os.path.splitext(path)[0] + ".mtl"
        if self._write(path, ("\n".join(lines) + "\n").encode("utf-8")):
            self._write(material, b"newmtl fake\n")
        return [path, material]

    def ExportSnapshot3D(self, path: str) -> list:
        self.calls.append(("ExportSnapshot3D", path))
        self._write(path, png_bytes(*self.snapshot_size))
        return [[path]]

    def ExportZPrj(self, path: str) -> bool:
        self.calls.append(("ExportZPrj", path))
        self._write(path, b"fake zprj")
        return True

    def ExportTurntableImages(self, path: str, count: int, width: int, height: int,
                              mode: int) -> list:
        # Measured: returns [] and writes no files.
        self.calls.append(("ExportTurntableImages", path))
        return []

    # ------------------------------------------------------------ utility_api

    def SetCamViewPoint(self, view: int) -> None:
        self.calls.append(("SetCamViewPoint", view))
        self.camera = view

    def Refresh3DWindow(self) -> None:
        self.calls.append(("Refresh3DWindow",))

    def SetPatternPieceName(self, index: int, name: str) -> None:
        self.calls.append(("SetPatternPieceName", index, name))
        self.piece_names[index] = name

    def CreateInternalShapeWithPoints(self, index: int, points, is_closed: bool) -> int:
        self.calls.append(("CreateInternalShapeWithPoints", index, len(points)))
        self.shape_counts[index] += 1
        return self.shape_counts[index] - 1

    def AddSeamlinePairGroup(self, *args) -> bool:
        self.calls.append(("AddSeamlinePairGroup", *args))
        if len(args) == 6:
            self.seams.append(tuple(args))
        else:
            self.seam_groups += 1
        return True

    def GetSeamlinePairGroupCount(self) -> int:
        return self.seam_groups + len(self.seams)

    def GetPatternCount(self) -> int:
        return len(self.piece_names)

    def GetPatternPieceName(self, index: int) -> str:
        return self.piece_names[index]

    def _recorder(self, name: str):
        def call(*args) -> None:
            self.calls.append((name, *args))

        return call

    # ------------------------------------------------------------ utility_api

    def RegisterPythonScript(self, path: str, title: str) -> bool:
        # Path first. Measured from MD's plugin log on 2026.0.315: given (title, path)
        # it reports "the script path is invalid" for the title, and registers nothing.
        self.calls.append(("RegisterPythonScript", path, title))
        if self.registration_error is not None:
            raise self.registration_error
        if not self.registration_result or not os.path.isfile(path):
            return False
        if any(registered == path for _, registered in self.registered):
            return False  # MD: "already registered", whatever the title
        self.registered.append((title, path))
        return True

    def DisplayMessageBox(self, message: str) -> None:
        # Modal in MD, and it holds the main thread until it is dismissed. Here it
        # returns at once, so nothing in these tests measures that.
        self.calls.append(("DisplayMessageBox", message))
        self.message_boxes.append(message)

    def NewProject(self) -> None:
        self.calls.append(("NewProject",))
        self.piece_names.clear()
        self.shape_counts.clear()
        self.piece_points.clear()
        self.placements.clear()
        self.seam_groups = 0
        self.seams.clear()
        self.avatars.clear()

    def modules(self) -> dict[str, types.ModuleType]:
        pattern_api = types.ModuleType("pattern_api")
        for name in (
            "CreatePatternWithPoints",
            "SetPatternPieceName",
            "CreateInternalShapeWithPoints",
            "AddSeamlinePairGroup",
            "GetPatternCount",
            "GetPatternPieceName",
            "GetSeamlinePairGroupCount",
            "GetLineLength",
            "DeletePatternPiece",
            "GetArrangementList",
            "SetArrangement",
            "ExportPatternJSON",
            "ExportObjectBrowserMaterialsList",
        ):
            setattr(pattern_api, name, getattr(self, name))
        for name in (
            "SetArrangementShapeStyle",
            "SetArrangementOrientation",
            "SetPatternLayer",
            "SetPatternPieceElasticTotalLength",
        ):
            setattr(pattern_api, name, self._recorder(name))

        utility_api = types.ModuleType("utility_api")
        utility_api.NewProject = self.NewProject
        utility_api.GetMajorVersion = lambda: 2026
        utility_api.GetMinorVersion = lambda: 0
        utility_api.GetPatchVersion = lambda: 315
        # Both forms, as the live build has: md-mcp prefers the wide one for paths.
        utility_api.RegisterPythonScript = self.RegisterPythonScript
        utility_api.RegisterPythonScriptW = self.RegisterPythonScript
        utility_api.DisplayMessageBox = self.DisplayMessageBox
        utility_api.SetCamViewPoint = self.SetCamViewPoint
        utility_api.Refresh3DWindow = self.Refresh3DWindow

        # Avatars come from export_api and fabrics from fabric_api -- not where either
        # belongs, and exactly why the scene readback reads the live build rather than
        # anyone's idea of where a call ought to live.
        export_api = types.ModuleType("export_api")
        export_api.GetAvatarNameList = lambda: list(self.avatars)
        for name in (
            "ExportOBJ",
            "ExportSnapshot3D",
            "ExportZPrj",
            "ExportTurntableImages",
        ):
            setattr(export_api, name, getattr(self, name))

        api_types = types.ModuleType("ApiTypes")
        api_types.ImportExportOption = ImportExportOption

        # pybind11 raises TypeError with this exact shape when no overload matches, and
        # md-mcp turns it into advice. Two kinds, because they need different advice:
        # one is a fixable arity mistake, the other cannot be fixed from md_call at all.
        def _no_overload_matches(*args) -> None:
            raise TypeError(
                "SetSimulationQuality(): incompatible function arguments. The "
                "following argument types are supported:\n    1. (arg0: int, arg1: "
                "int) -> None\n\nInvoked with: 1"
            )

        def _wants_an_md_object(*args) -> None:
            raise TypeError(
                "ImportZprj(): incompatible function arguments. The following "
                "argument types are supported:\n    1. (arg0: str, arg1: "
                "Marvelous::ImportZPRJOption) -> bool\n\nInvoked with: 'C:/x.zprj'"
            )

        utility_api.SetSimulationQuality = _no_overload_matches

        import_api = types.ModuleType("import_api")
        import_api.ImportZprj = _wants_an_md_object

        fabric_api = types.ModuleType("fabric_api")
        fabric_api.GetFabricStyleNameList = lambda: list(self.fabrics)
        # The broken one, present so a test can prove the listener does not use it.
        fabric_api.GetFabricCount = lambda: 0

        return {
            "pattern_api": pattern_api,
            "utility_api": utility_api,
            "export_api": export_api,
            "import_api": import_api,
            "fabric_api": fabric_api,
            "ApiTypes": api_types,
        }


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def load_listener(namespace: dict, listener: pathlib.Path = LISTENER) -> dict:
    """Execute the shipped listener into ``namespace`` without starting it.

    Run by MD, the file registers its entries and starts serving straight away. Loading
    and starting are separate here so the tests can start it with a timer they can
    drive -- see `run_listener`.

    ``listener`` is the file under test. It defaults to the one in the checkout and is
    pointed at the packaged one by ``tests/test_packaging.py``: what a release ships is
    a stamped copy, and a copy is not covered by having tested the original.

    ``MD_MCP_AUTORUN`` is how loading is told apart from running. The file does not
    check for ``__main__`` -- what MD sets for a clicked plugin is unmeasured, and a
    plugin that silently did nothing would be the worst of the failures available -- so
    it runs unless something says otherwise, and this is the something.
    """
    namespace.setdefault("MD_MCP_AUTORUN", False)
    namespace.setdefault("__name__", "__md_mcp_listener__")
    namespace.setdefault("__file__", str(listener))
    exec(
        compile(listener.read_text(encoding="utf-8"), str(listener), "exec"), namespace
    )
    return namespace


def run_listener(namespace: dict, listener: pathlib.Path = LISTENER) -> dict:
    """Load the shipped listener and start it, ticked by a thread instead of Windows.

    Inside MD a user32 timer calls the listener's tick from MD's message loop. A test
    runner pumps no messages, so that timer would never fire here; the listener's own
    ``_ThreadTimer`` calls the same tick from a thread, which is faithful for everything
    but the threading itself.
    """
    load_listener(namespace, listener)
    namespace["start"](timer=namespace["_ThreadTimer"])
    return namespace


def load_stopper(namespace: dict, stopper: pathlib.Path = STOPPER) -> dict:
    """Execute the shipped Stop MCP script into ``namespace`` without running it."""
    namespace.setdefault("MD_MCP_AUTORUN", False)
    namespace.setdefault("__file__", str(stopper))
    exec(compile(stopper.read_text(encoding="utf-8"), str(stopper), "exec"), namespace)
    return namespace


@contextlib.contextmanager
def fake_md(monkeypatch) -> Iterator[FakeMDHost]:
    """A fake MD in ``sys.modules``, with no listener running.

    For the parts of the listener that are not the serve loop -- registering the
    Plugin tab button is the one -- which need MD's modules present and nothing bound.
    """
    host = FakeMDHost()
    for name, module in host.modules().items():
        monkeypatch.setitem(sys.modules, name, module)
    yield host


def stop_listener(port: int) -> None:
    with contextlib.suppress(OSError):
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as client:
            client.sendall(b'{"op": "shutdown"}\n')
            client.recv(4096)


@contextlib.contextmanager
def running_listener(
    monkeypatch,
    port: int | None = None,
    namespace: dict | None = None,
    listener: pathlib.Path = LISTENER,
) -> Iterator[FakeMDHost]:
    """Run the shipped listener in-process against a fake MD, on a free port."""
    host = FakeMDHost()
    for name, module in host.modules().items():
        monkeypatch.setitem(sys.modules, name, module)

    attempts = 1 if port is not None else 5
    for attempt in range(attempts):
        chosen = port if port is not None else free_port()
        monkeypatch.setenv("MD_MCP_PORT", str(chosen))
        try:
            run_listener({} if namespace is None else namespace, listener)
            break
        except (OSError, RuntimeError):
            # free_port() only proves the port was free a moment ago; something else can
            # take it before the listener binds. Pick another rather than failing a
            # bridge test for a reason that has nothing to do with the bridge. A foreign
            # holder that does not answer a shutdown raises RuntimeError, not OSError --
            # that is the likelier of the two.
            if attempt == attempts - 1:
                raise
    try:
        yield host
    finally:
        stop_listener(chosen)
