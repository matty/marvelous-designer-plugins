"""The packaged plugin, built and then run.

Two claims are worth tests here, and they are different claims.

**The build refuses what MD could not run.** A listener that parses under the build
machine's interpreter but not MD's 3.11, or that imports something MD has no way to
supply, fails inside MD -- on a click, in a window someone has to go and find. Every
refusal below is a failure the build script is supposed to catch first.

**What ships is what was tested.** The release carries a *copy* of the listener with
its version stamped in, and a copy is not covered by having tested the original. So the
packaged file is unzipped and served against the fake MD host, exactly as
``tests/test_bridge.py`` does to the checkout's copy.

**The window is driven, not drawn.** The control window has no event loop of its own --
the serve loop calls its ``tick`` -- so the loop can be tested against a window that
records instead of drawing, buttons and all. What that cannot show is a window opening
inside MD, which only a live MD can.

What none of it proves is that Marvelous Designer accepts any of this: nothing here has
an MD. ``utility_api.RegisterPythonScript`` is faked, and the fake says yes.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import pathlib
import time
import zipfile

import pytest

from md_mcp import bridge
from tests.fake_md_host import (
    fake_md,
    free_port,
    load_listener,
    running_listener,
    stop_listener,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
LISTENER = ROOT / "plugin" / "md_mcp_listener.py"


def _load_build_script():
    """Import ``tools/build_plugin.py``, which is a script and not a package."""
    spec = importlib.util.spec_from_file_location(
        "build_plugin", ROOT / "tools" / "build_plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_plugin = _load_build_script()

#: A stand-in for a real short hash, so the tests do not depend on where HEAD is.
COMMIT = "0123456789ab"


@pytest.fixture(scope="module")
def package(tmp_path_factory) -> pathlib.Path:
    """The zip, built once, plus the unpacked copy the build leaves beside it."""
    return build_plugin.build(tmp_path_factory.mktemp("dist"), commit=COMMIT)


@pytest.fixture(scope="module")
def unpacked(package: pathlib.Path) -> pathlib.Path:
    return package.with_suffix("")


def _wait_for_ping(timeout: float = 5.0) -> dict:
    """Poll until the listener answers. A thread takes a moment to bind its port."""
    deadline = time.time() + timeout
    while True:
        try:
            return bridge.ping()
        except bridge.BridgeError:
            if time.time() > deadline:
                raise
            time.sleep(0.02)


def _wait_until_silent(timeout: float = 5.0) -> bool:
    """Poll until the listener stops answering. It stops at the top of its next loop."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            bridge.ping()
        except bridge.BridgeError:
            return True
        time.sleep(0.02)
    return False


def _member(package: pathlib.Path, name: str) -> bytes:
    with zipfile.ZipFile(package) as archive:
        folder = package.stem
        return archive.read(f"{folder}/{name}")


# ------------------------------------------------------------------- what is in the zip


def test_the_package_holds_everything_an_install_needs(package: pathlib.Path) -> None:
    with zipfile.ZipFile(package) as archive:
        names = {pathlib.PurePosixPath(n).name for n in archive.namelist()}

    assert names == {"md_mcp_listener.py", "install.ps1", "README.md", "manifest.json"}


def test_the_listener_says_which_version_and_commit_it_is(package: pathlib.Path) -> None:
    version = build_plugin.project_version()
    source = _member(package, "md_mcp_listener.py").decode("utf-8")

    assert f'__version__ = "{version}"' in source
    assert f'BUILD = "{COMMIT}"' in source
    # The placeholders are gone, not merely accompanied.
    assert build_plugin.VERSION_PLACEHOLDER not in source


def test_the_manifest_hashes_what_the_package_actually_contains(
    package: pathlib.Path,
) -> None:
    manifest = json.loads(_member(package, "manifest.json"))

    assert manifest["version"] == build_plugin.project_version()
    assert manifest["commit"] == COMMIT
    for name, digest in manifest["files"].items():
        actual = hashlib.sha256(_member(package, name)).hexdigest()
        assert actual == digest, f"{name} does not match its manifest hash"


def test_the_sha256_beside_the_zip_is_the_zip(package: pathlib.Path) -> None:
    raw = package.with_suffix(".zip.sha256").read_bytes()
    digest, _, name = raw.decode("utf-8").strip().partition("  ")

    assert digest == hashlib.sha256(package.read_bytes()).hexdigest()
    assert name == package.name
    # Written on Windows as often as not, and `sha256sum -c` takes a carriage return
    # to be part of the filename it is looking for.
    assert b"\r" not in raw


def test_rebuilding_the_same_commit_gives_the_same_bytes(
    package: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    # A published hash is only worth printing if somebody else can arrive at it. What
    # this catches is anything that varies with *when* or *where* the build ran -- a
    # build date in the manifest is the tempting one.
    again = build_plugin.build(tmp_path, commit=COMMIT)

    assert again.read_bytes() == package.read_bytes()


def test_the_package_carries_no_timestamps(package: pathlib.Path) -> None:
    # Separately from the comparison above, which cannot see this: a zip member's
    # recorded time has two-second granularity, so two builds a moment apart agree
    # whether or not anyone asked them to. Only the last build of the day would differ,
    # and a reproducibility check that fails at midnight is worse than none.
    with zipfile.ZipFile(package) as archive:
        stamps = {info.date_time for info in archive.infolist()}

    assert stamps == {(1980, 1, 1, 0, 0, 0)}


# ----------------------------------------------------------------- what it refuses to do


def test_a_listener_md_could_not_parse_is_not_packaged() -> None:
    # `type X = int` is 3.12 syntax. MD 2026.0.315 embeds 3.11.8, so this parses on a
    # modern build machine and is a SyntaxError in the only interpreter that matters.
    with pytest.raises(build_plugin.BuildError, match="3.11"):
        build_plugin.check_runs_in_md("type Alias = int\n", "md_mcp_listener.py")


def test_a_listener_importing_what_md_cannot_supply_is_not_packaged() -> None:
    with pytest.raises(build_plugin.BuildError, match="requests"):
        build_plugin.check_runs_in_md("import requests\n", "md_mcp_listener.py")


def test_even_an_import_that_is_allowed_to_fail_is_refused() -> None:
    # A try/except around it would make the plugin behave one way here and another way
    # on a machine that happens to have the package. One file, no dependencies, same
    # window everywhere -- PySide6 was tried inside MD and this is the conclusion.
    with pytest.raises(build_plugin.BuildError, match="PySide6"):
        build_plugin.check_runs_in_md(
            "try:\n"
            "    import PySide6\n"
            "except Exception:\n"
            "    PySide6 = None\n",
            "md_mcp_listener.py",
        )


def test_mds_own_modules_are_not_mistaken_for_missing_dependencies() -> None:
    build_plugin.check_runs_in_md(
        "import json\nimport pattern_api\nimport utility_api\n", "md_mcp_listener.py"
    )


def test_a_listener_with_nothing_to_stamp_is_not_packaged() -> None:
    with pytest.raises(build_plugin.BuildError, match="__version__"):
        build_plugin.stamp("print('hello')\n", "1.2.3", COMMIT)


def test_the_checkout_is_stampable(tmp_path: pathlib.Path) -> None:
    # Guards the pair of tests above against passing on a listener that stopped being
    # buildable for some other reason -- a stamp that matches nothing and a stamp that
    # matches twice both raise, and only the first is what those tests mean.
    stamped = build_plugin.stamp(
        LISTENER.read_text(encoding="utf-8"), "1.2.3", COMMIT
    )

    assert '__version__ = "1.2.3"' in stamped


# ------------------------------------------------------- what ships is what was tested


def test_the_packaged_listener_serves_md(monkeypatch, unpacked: pathlib.Path) -> None:
    listener = unpacked / "md_mcp_listener.py"

    with running_listener(monkeypatch, listener=listener):
        reply = bridge.ping()

    assert reply["md_version"] == "2026.0.315"
    assert reply["plugin_version"] == build_plugin.project_version()
    assert reply["plugin_build"] == COMMIT
    # Which file answered, not which file is newest on disk: MD keeps a registered
    # plugin's path for good, so a stale install answers exactly as confidently.
    assert pathlib.Path(reply["plugin_path"]) == listener


def test_the_checkouts_listener_admits_it_is_not_a_release(monkeypatch) -> None:
    with running_listener(monkeypatch):
        reply = bridge.ping()

    assert reply["plugin_version"] == "0.0.0+source"
    assert reply["plugin_build"] == "source checkout"


# --------------------------------------------------------------- the Plugin tab entry


@pytest.fixture
def listener(monkeypatch):
    """The listener loaded against a fake MD, with nothing bound and nothing serving."""
    with fake_md(monkeypatch) as host:
        yield host, load_listener({})


def _register(namespace, tmp_path, **kwargs):
    return namespace["register_plugin"](
        marker=str(tmp_path / "registered.json"), **kwargs
    )


def test_registering_puts_this_one_file_in_mds_plugin_tab(listener, tmp_path) -> None:
    # One entry, not three. MD's Plug-in Manager adds one .py at a time and MD has no
    # call that removes one, so every entry is something a person adds by hand if the
    # API refuses, and can never take back.
    host, namespace = listener

    message = _register(namespace, tmp_path)

    assert host.registered == [("md-mcp", str(LISTENER))]
    assert "Plugin tab" in message


def test_registering_twice_asks_md_once(listener, tmp_path) -> None:
    # MD does not say whether it already had a script, so asking twice is how the
    # Plugin tab ends up with two of everything and no way to remove either.
    host, namespace = listener

    _register(namespace, tmp_path)
    message = _register(namespace, tmp_path)

    assert len(host.registered) == 1
    assert "already in MD's Plugin tab" in message


def test_an_entry_md_declines_is_reported_and_not_recorded(listener, tmp_path) -> None:
    # Measured on 2026.0.315: RegisterPythonScript answers False -- registering
    # nothing, raising nothing -- for every call made from a script running inside
    # another one. Recording that as success means never offering the entry again.
    host, namespace = listener
    host.registration_result = False

    message = _register(namespace, tmp_path)

    assert host.registered == []
    assert "would not register" in message
    assert "Plug-in Manager" in message
    assert not (tmp_path / "registered.json").exists()


def test_a_refused_registration_costs_a_button_and_not_a_bridge(
    listener, tmp_path
) -> None:
    host, namespace = listener
    host.registration_error = RuntimeError("no such call")

    message = _register(namespace, tmp_path)

    assert host.registered == []
    assert "Plug-in Manager" in message
    assert not (tmp_path / "registered.json").exists()


def test_a_listener_with_no_path_registers_nothing(monkeypatch, tmp_path) -> None:
    # Pasting the file's contents into MD's editor instead of the two-line bootstrap:
    # it runs, and there is no path to give MD. An entry pointing at nothing is a
    # button that fails when clicked.
    with fake_md(monkeypatch) as host:
        namespace = load_listener({"__file__": None})

        message = _register(namespace, tmp_path)

    assert host.registered == []
    assert "no __file__" in message


# --------------------------------------------------------------- the control window


class FakePanel:
    """A control window that records instead of drawing one.

    Buttons are *queued* and pressed inside ``tick``, because that is where Tk fires
    them: the loop calls ``update()``, and the callbacks run there, on the loop's own
    thread. A test that called start_serving directly would be testing something that
    cannot happen inside MD.
    """

    def __init__(self, state, namespace):
        self._state = state
        self._namespace = namespace
        self._pending: list[str] = []
        self.ticks = 0
        self.raised = 0
        self.closed = False

    def press(self, button: str) -> None:
        self._pending.append(button)

    def user_closes(self) -> None:
        self.closed = True

    # ------------------------------------------------ what the loop calls

    def tick(self) -> bool:
        while self._pending:
            button = self._pending.pop(0)
            self._namespace["start_serving" if button == "start" else "stop_serving"](
                self._state
            )
        self.ticks += 1
        return not self.closed

    def raise_window(self) -> None:
        self.raised += 1

    def close(self) -> None:
        self.closed = True


@contextlib.contextmanager
def panelled_listener(monkeypatch, factory=None, press_start=True):
    """The listener running with a control window, on a port of its own.

    With a window, opening the plugin no longer opens the port -- Start does -- so the
    default here is to press it, which is what a person does a second after the window
    appears. ``press_start=False`` leaves it idle.
    """
    with fake_md(monkeypatch) as host:
        namespace = load_listener({})
        port = free_port()
        monkeypatch.setenv("MD_MCP_PORT", str(port))
        panels: list[FakePanel] = []

        def make(state):
            panel = FakePanel(state, namespace) if factory is None else factory(state)
            if panel is not None:
                panels.append(panel)
            return panel

        thread = namespace["start"](block=False, port=port, panel_factory=make)
        try:
            if press_start:
                _wait_for_panel(panels)
                panels[0].press("start")
                # free_port() only proves the port was free a moment ago, and with a
                # window a port that is taken is reported rather than raised -- so the
                # symptom is this timing out, in whichever test happened to be running.
                try:
                    _wait_for_ping()
                except bridge.BridgeError as exc:
                    raise AssertionError(
                        "the listener never answered on {}: {}".format(
                            port, panels[0]._state["last"] or exc
                        )
                    ) from exc
            yield host, namespace, panels, port
        finally:
            stop_listener(port)
            for panel in panels:
                panel.closed = True
            thread.join(timeout=5)


def _wait_for_panel(panels, timeout: float = 5.0):
    """Wait for the loop to build its window. It does that on its first tick."""
    deadline = time.time() + timeout
    while not panels and time.time() < deadline:
        time.sleep(0.02)
    assert panels, "the loop never built a window"
    return panels[0]


def test_the_window_opens_without_listening(monkeypatch) -> None:
    # Opening the plugin opens the window and nothing else. Clicking a thing called
    # md-mcp should not put a port on the network by itself -- and the bridge runs
    # arbitrary Python inside MD, so "by itself" is the part that matters.
    with panelled_listener(monkeypatch, press_start=False) as (_, _, panels, _):
        _wait_for_panel(panels)
        deadline = time.time() + 0.5
        while time.time() < deadline:
            with pytest.raises(bridge.BridgeError):
                bridge.ping()
            time.sleep(0.05)

        assert panels and panels[0].ticks, "the window should be up and refreshing"
        assert panels[0]._state["last"] == "press Start to listen"


def test_start_in_the_window_is_what_opens_the_port(monkeypatch) -> None:
    with panelled_listener(monkeypatch, press_start=False) as (_, _, panels, _):
        _wait_for_panel(panels).press("start")

        assert _wait_for_ping()["ok"]


def test_without_a_window_it_listens_immediately(monkeypatch) -> None:
    # The other half of that rule: with no window there is no Start to press, so a
    # plugin that waited for one would do nothing at all for ever.
    with running_listener(monkeypatch):
        assert bridge.ping()["ok"]


def test_the_window_opens_and_start_makes_it_serve(monkeypatch) -> None:
    with panelled_listener(monkeypatch) as (_, _, panels, _):
        assert bridge.ping()["ok"]

        assert len(panels) == 1, "the loop should build exactly one window"
        assert panels[0].ticks, "the window is refreshed by the serve loop, not itself"


def test_stop_in_the_window_closes_the_port_and_keeps_the_window(monkeypatch) -> None:
    with panelled_listener(monkeypatch) as (_, _, panels, _):
        panel = panels[0]
        before = panel.ticks

        panel.press("stop")

        assert _wait_until_silent(), "Stop left the port open"
        assert not panel.closed
        assert panel.ticks > before, "the loop stopped running when the bridge did"


def test_start_in_the_window_binds_the_port_again(monkeypatch) -> None:
    # The whole point of a window over a toggle: stopping is not the end of the script,
    # so starting again is a button rather than a paste.
    with panelled_listener(monkeypatch) as (_, _, panels, _):
        panel = panels[0]
        panel.press("stop")
        assert _wait_until_silent()

        panel.press("start")

        assert _wait_for_ping()["ok"]


def test_closing_the_window_leaves_the_bridge_serving(monkeypatch) -> None:
    # A control panel, not a leash. Somebody tidying their desktop must not take the
    # bridge down with the window.
    with panelled_listener(monkeypatch) as (_, _, panels, _):
        panels[0].user_closes()

        time.sleep(0.2)
        assert bridge.ping()["ok"]


def test_closing_the_window_with_nothing_serving_ends_the_script(monkeypatch) -> None:
    # And the other way: a script with nothing to do must return. MD only runs Python
    # while a script is running, so a loop with no socket and no window is holding MD's
    # main thread for nobody.
    with fake_md(monkeypatch):
        namespace = load_listener({})
        port = free_port()
        monkeypatch.setenv("MD_MCP_PORT", str(port))
        panels: list[FakePanel] = []

        def make(state):
            panels.append(FakePanel(state, namespace))
            return panels[-1]

        thread = namespace["start"](block=False, port=port, panel_factory=make)
        _wait_for_panel(panels).press("start")
        _wait_for_ping()

        panels[0].press("stop")
        assert _wait_until_silent()
        panels[0].user_closes()

        thread.join(timeout=5)

    assert not thread.is_alive(), "the loop held the main thread with nothing to do"


def test_running_it_again_brings_the_window_back(monkeypatch) -> None:
    # A second run executes nested inside the first one's loop and can only leave a
    # flag. With a window, the useful flag is "show yourself" -- the old behaviour,
    # stopping, is what a Plugin tab click must not do.
    with panelled_listener(monkeypatch) as (_, namespace, panels, port):
        panels[0].user_closes()
        time.sleep(0.2)

        assert namespace["start"](port=port, panel_factory=lambda state: None) is None

        deadline = time.time() + 5
        while time.time() < deadline and len(panels) < 2:
            time.sleep(0.02)
        assert bridge.ping()["ok"], "running it again stopped the bridge"
        assert len(panels) == 2, "running it again did not put a window back"


def test_a_window_that_will_not_open_still_serves(monkeypatch) -> None:
    # tkinter is importable in MD 2026.0.315, but nothing here has watched a Tk window
    # open inside MD. If it will not, the bridge is the half that matters.
    def refuses(state):
        state["panel_error"] = "no control window -- tkinter would not open one"
        return None

    with panelled_listener(monkeypatch, factory=refuses, press_start=False) as (
        host,
        _,
        _,
        _,
    ):
        assert _wait_for_ping()["ok"]

    # Said where it will be seen: where a clicked plugin's print output goes is not
    # known, so anything that decides whether the plugin is usable gets a message box.
    assert any("no control window" in box for box in host.message_boxes)
