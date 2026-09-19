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
import pathlib
import socket
import sys
import types
from typing import Iterator

LISTENER = pathlib.Path(__file__).resolve().parents[1] / "plugin" / "md_mcp_listener.py"


class FakeMDHost:
    """Records the calls an emitted script makes, and answers the readback calls."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.piece_names: list[str] = []
        self.shape_counts: list[int] = []
        self.seam_groups = 0
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
        #: that registers nothing and raises nothing. Measured on 2026.0.315: that is
        #: what MD does to every registration made from a script running inside another.
        self.registration_result: bool = True
        #: Every DisplayMessageBox. The Stop and Status entries report through one,
        #: because where a clicked plugin's stdout goes has not been measured.
        self.message_boxes: list[str] = []

    # ------------------------------------------------------------ pattern_api

    def CreatePatternWithPoints(self, points) -> int:
        self.calls.append(("CreatePatternWithPoints", len(points)))
        self.piece_names.append(f"pattern-{len(self.piece_names)}")
        self.shape_counts.append(0)
        return len(self.piece_names) - 1

    def SetPatternPieceName(self, index: int, name: str) -> None:
        self.calls.append(("SetPatternPieceName", index, name))
        self.piece_names[index] = name

    def CreateInternalShapeWithPoints(self, index: int, points, is_closed: bool) -> int:
        self.calls.append(("CreateInternalShapeWithPoints", index, len(points)))
        self.shape_counts[index] += 1
        return self.shape_counts[index] - 1

    def AddSeamlinePairGroup(self, *args) -> bool:
        self.calls.append(("AddSeamlinePairGroup", *args))
        self.seam_groups += 1
        return True

    def GetSeamlinePairGroupCount(self) -> int:
        return self.seam_groups

    def GetPatternCount(self) -> int:
        return len(self.piece_names)

    def GetPatternPieceName(self, index: int) -> str:
        return self.piece_names[index]

    def _recorder(self, name: str):
        def call(*args) -> None:
            self.calls.append((name, *args))

        return call

    # ------------------------------------------------------------ utility_api

    def RegisterPythonScript(self, name: str, path: str) -> bool:
        self.calls.append(("RegisterPythonScript", name, path))
        if self.registration_error is not None:
            raise self.registration_error
        if not self.registration_result:
            return False
        self.registered.append((name, path))
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
        self.seam_groups = 0
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

        # Avatars come from export_api and fabrics from fabric_api -- not where either
        # belongs, and exactly why the scene readback reads the live build rather than
        # anyone's idea of where a call ought to live.
        export_api = types.ModuleType("export_api")
        export_api.GetAvatarNameList = lambda: list(self.avatars)

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
        }


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def load_listener(namespace: dict, listener: pathlib.Path = LISTENER) -> dict:
    """Execute the shipped listener into ``namespace`` without starting it.

    The file only serves when run as ``__main__`` -- which is how MD's bootstrap runs
    it, and how it must be, because in MD ``start()`` never returns. Loading and
    starting are separate here so the tests can do the second part on a thread.

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
    """Load the shipped listener and start it serving on a background thread.

    Inside MD the loop owns the main thread and pumping keeps MD alive; here there is
    no main thread to hold and no message queue to pump, so a thread is both faithful
    enough for the protocol and the only way the test can go on to make a request.
    """
    load_listener(namespace, listener)
    namespace["start"](block=False)
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
