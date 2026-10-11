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

**Start and Stop are clicked, not drawn.** Start MCP is the listener's ``main()`` and
Stop MCP is ``md_mcp_stop.py``; both run here against the fake MD, with the timer
ticked by a thread. What that cannot show is a Windows timer firing inside MD, which
only a live MD can.

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
    load_stopper,
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

    assert names == {
        "md_mcp_listener.py",
        "md_mcp_stop.py",
        "install.ps1",
        "README.md",
        "manifest.json",
    }


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


def test_the_stop_script_is_held_to_the_same_rules(tmp_path: pathlib.Path) -> None:
    # Stop MCP is run by MD exactly as Start MCP is, so an import MD cannot supply
    # fails on the click just the same.
    (tmp_path / "plugin").mkdir()
    for name in ("md_mcp_listener.py", "install.ps1", "README.md"):
        (tmp_path / "plugin" / name).write_bytes((ROOT / "plugin" / name).read_bytes())
    (tmp_path / "plugin" / "md_mcp_stop.py").write_text("import requests\n")

    with pytest.raises(build_plugin.BuildError, match="md_mcp_stop.py imports"):
        build_plugin.contents("1.2.3", COMMIT, root=tmp_path)


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


def test_the_packaged_stop_entry_stops_the_packaged_listener(
    monkeypatch, unpacked: pathlib.Path
) -> None:
    shared: dict = {}

    with running_listener(
        monkeypatch, namespace=shared, listener=unpacked / "md_mcp_listener.py"
    ) as host:
        port = bridge.port()
        load_stopper({}, unpacked / "md_mcp_stop.py")["main"]()

        assert _wait_until_silent(), "the packaged Stop MCP left the port open"
        assert shared["running"](port) is None
        assert any("stopped listening" in box for box in host.message_boxes)


# -------------------------------------------------------------- the Plugin tab entries


@pytest.fixture
def listener(monkeypatch):
    """The listener loaded against a fake MD, with nothing bound and nothing serving."""
    with fake_md(monkeypatch) as host:
        yield host, load_listener({})


def _register(namespace, tmp_path, **kwargs):
    return namespace["register_plugin"](
        marker=str(tmp_path / "registered.json"), **kwargs
    )


def test_registering_puts_start_and_stop_in_mds_plugin_tab(listener, tmp_path) -> None:
    # Two entries and no more. MD's Plug-in Manager adds one .py at a time and MD has no
    # call that removes one, so every entry is something a person may add by hand if
    # the API refuses, and can only take back in Plug-in Manager.
    host, namespace = listener

    message = _register(namespace, tmp_path)

    assert host.registered == [
        ("Start MCP", str(LISTENER)),
        ("Stop MCP", str(LISTENER.with_name("md_mcp_stop.py"))),
    ]
    assert "Plugin tab" in message


def test_registering_twice_asks_md_once(listener, tmp_path) -> None:
    # MD does not say whether it already had a script, so asking twice is how the
    # Plugin tab ends up with two of everything and no way to remove either.
    host, namespace = listener

    _register(namespace, tmp_path)
    message = _register(namespace, tmp_path)

    assert len(host.registered) == 2
    assert "already in MD's Plugin tab" in message


def test_an_entry_md_declines_is_reported_and_not_recorded(listener, tmp_path) -> None:
    # Measured on 2026.0.315: RegisterPythonScript answers False -- registering
    # nothing, raising nothing -- for an invalid path or one it already has.
    # Recording that as success means never offering the entry again.
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


def test_a_listener_without_its_stop_script_registers_start_and_says_so(
    monkeypatch, tmp_path
) -> None:
    # A Stop MCP entry pointing at a file that is not there is a button that fails
    # when clicked, so it is not registered -- and the missing file is named.
    lonely = tmp_path / "plugin" / "md_mcp_listener.py"
    lonely.parent.mkdir()
    lonely.write_bytes(LISTENER.read_bytes())

    with fake_md(monkeypatch) as host:
        namespace = load_listener({}, lonely)

        message = _register(namespace, tmp_path)

    assert host.registered == [("Start MCP", str(lonely))]
    assert "md_mcp_stop.py" in message


# ------------------------------------------------------------ clicking Start and Stop


@contextlib.contextmanager
def clicked_start(monkeypatch, tmp_path):
    """MD running Start MCP: ``main()``, with a timer a test runner can drive.

    LOCALAPPDATA is pointed at the test's own folder, because main() records what it
    registered there, and a test must never write to the real record.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with fake_md(monkeypatch) as host:
        namespace = load_listener({})
        namespace["_default_timer"] = lambda: namespace["_ThreadTimer"]
        port = free_port()
        namespace["PORT"] = port
        monkeypatch.setenv("MD_MCP_PORT", str(port))
        try:
            yield host, namespace, port
        finally:
            stop_listener(port)


def test_start_mcp_registers_both_entries_listens_and_returns(
    monkeypatch, tmp_path
) -> None:
    with clicked_start(monkeypatch, tmp_path) as (host, namespace, port):
        assert namespace["main"]() is None  # it returns: the timer does the serving

        assert _wait_for_ping()["ok"]
        assert [name for name, _ in host.registered] == ["Start MCP", "Stop MCP"]
        # Said where it will be seen, with the entries' news, in one box.
        assert len(host.message_boxes) == 1
        assert "listening on 127.0.0.1:{}".format(port) in host.message_boxes[0]
        assert "Start MCP" in host.message_boxes[0]


def test_clicking_start_mcp_again_leaves_it_serving(monkeypatch, tmp_path) -> None:
    with clicked_start(monkeypatch, tmp_path) as (host, namespace, _):
        namespace["main"]()
        _wait_for_ping()
        first = namespace["running"]()
        host.message_boxes.clear()

        namespace["main"]()

        # The same listener, not a replacement that shut the first one down to bind.
        assert namespace["running"]() is first
        assert bridge.ping()["ok"]
        # Nothing needs attention, so nothing to dismiss: a routine click costs no box.
        assert host.message_boxes == []


def test_start_mcp_with_the_port_taken_says_so(monkeypatch, tmp_path) -> None:
    import socket

    def refuses(port):
        raise RuntimeError("port {} is still in use and did not answer".format(port))

    with clicked_start(monkeypatch, tmp_path) as (host, namespace, port):
        # Something that is not md-mcp holds the port and never answers a shutdown.
        squatter = socket.socket()
        squatter.bind(("127.0.0.1", port))
        squatter.listen(1)
        namespace["_stop_foreign"] = refuses
        try:
            namespace["main"]()
        finally:
            squatter.close()

        assert namespace["running"]() is None
        assert any("could not listen" in box for box in host.message_boxes)
