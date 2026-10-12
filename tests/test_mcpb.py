"""The MCPB bundle's manifest, lock and launch line, checked without building one.

Building the bundle downloads a Python runtime and every wheel, and only a Windows
machine can run what it produces, so ``tools/build_mcpb.py`` checks the finished bundle
itself -- it unpacks it, launches it as the manifest says and lists its tools. What is
checked here is everything that can go wrong before that: a manifest the MCPB schema
would reject, a tool list that is not the server's, environment variables the server
does not read, and a lock that has lost one of pyproject's dependencies.

The rest of this file is the server's side of what the bundle relies on: MD_MCP_PORT
arriving from a host's number field, and the setup hint md_status gives when MD is not
listening.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
import tomllib

import anyio
import pytest
from mcp import Client

from md_mcp import __version__, bridge, checkpoints
from md_mcp.server import build_server

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_build_script():
    spec = importlib.util.spec_from_file_location(
        "build_mcpb", ROOT / "tools" / "build_mcpb.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_mcpb = _load_build_script()


@pytest.fixture(scope="module")
def manifest():
    return build_mcpb.manifest(__version__, build_mcpb.tool_list())


def test_manifest_matches_the_mcpb_schema(manifest):
    build_mcpb.validate(manifest)


def test_the_schema_check_refuses_what_mcpb_would(manifest):
    """Seen failing: a key v0.4 does not have, and a server type it does not know."""
    with pytest.raises(build_mcpb.BuildError, match="permissions"):
        build_mcpb.validate({**manifest, "permissions": {}})
    broken = {**manifest, "server": {**manifest["server"], "type": "exe"}}
    with pytest.raises(build_mcpb.BuildError, match="server/type"):
        build_mcpb.validate(broken)


def test_manifest_lists_exactly_the_servers_tools(manifest):
    async def listed():
        async with Client(build_server()) as client:
            return (await client.list_tools()).tools

    served = sorted(tool.name for tool in anyio.run(listed))
    assert sorted(tool["name"] for tool in manifest["tools"]) == served
    assert all(tool["description"] for tool in manifest["tools"])


def test_manifest_version_is_the_projects(manifest):
    with (ROOT / "pyproject.toml").open("rb") as handle:
        assert manifest["version"] == tomllib.load(handle)["project"]["version"]


def test_manifest_sets_only_variables_the_server_reads(manifest):
    env = manifest["server"]["mcp_config"]["env"]
    assert set(env) == {bridge.PORT_ENV, "MD_MCP_CHECKPOINTS"}
    for value in env.values():
        key = re.fullmatch(r"\$\{user_config\.(\w+)\}", value).group(1)
        assert key in manifest["user_config"]


def test_manifest_is_windows_only_and_launches_the_bundled_interpreter(manifest):
    config = manifest["server"]["mcp_config"]
    assert manifest["compatibility"]["platforms"] == ["win32"]
    assert config["command"].startswith("${__dirname}")
    # -I keeps the host's working directory off sys.path.
    assert config["args"] == ["-I", "-m", "md_mcp.server"]


def test_bundled_interpreter_finds_the_server_and_processes_pth_files():
    # ..\server is where stage() puts md_mcp; `import site` runs pywin32.pth. Dropping
    # the latter was seen to fail the build's smoke test with "No module named
    # 'pywintypes'".
    assert r"..\server" in build_mcpb.PTH_LINES
    assert "import site" in build_mcpb.PTH_LINES


def test_lock_pins_every_dependency_with_a_hash():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        wanted = tomllib.load(handle)["project"]["dependencies"]
    text = build_mcpb.LOCK.read_text(encoding="utf-8")
    pins = re.findall(r"^([a-z0-9-]+)==\S+ \\\n    --hash=sha256:[0-9a-f]{64}$", text, re.M)
    assert len(pins) == text.count("==")
    for requirement in wanted:
        name = re.match(r"[A-Za-z0-9_.-]+", requirement).group(0).lower()
        assert re.sub(r"[-_.]+", "-", name) in pins


def test_checkpoint_default_is_the_servers_own(manifest, monkeypatch):
    monkeypatch.delenv("MD_MCP_CHECKPOINTS", raising=False)
    default = manifest["user_config"]["checkpoints"]["default"]
    assert default.replace("${HOME}\\", "").split("\\") == [
        "AppData", "Local", *checkpoints.folder().parts[-2:]
    ]


# --- MD_MCP_PORT, as an MCPB host passes it --------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"), [("", 5121), ("  ", 5121), ("5121", 5121), ("6000.0", 6000)]
)
def test_port_reads_what_a_host_passes(monkeypatch, raw, expected):
    monkeypatch.setenv(bridge.PORT_ENV, raw)
    assert bridge.port() == expected


@pytest.mark.parametrize("raw", ["abc", "5121.5", "0", "70000", "${user_config.port}"])
def test_port_refuses_what_is_not_one(monkeypatch, raw):
    monkeypatch.setenv(bridge.PORT_ENV, raw)
    with pytest.raises(bridge.BridgeError, match="not a port"):
        bridge.port()


# --- the setup hint ---------------------------------------------------------------


def test_not_listening_gives_the_lines_to_paste(monkeypatch, tmp_path):
    listener = tmp_path / "with space" / "md_mcp_listener.py"
    listener.parent.mkdir()
    listener.write_text("", encoding="utf-8")
    monkeypatch.setenv(bridge.PLUGIN_ENV, str(listener))

    hint = bridge.how_to_connect()
    lines = bridge.paste_lines(listener)
    assert lines in hint
    # What is pasted has to run: valid Python, naming that exact file.
    tree = ast.parse(lines)
    assert tree.body[0].value.value == str(listener)


def test_paste_lines_take_a_windows_path_with_spaces():
    # A raw string cannot end in a backslash, and a Windows path never does here --
    # it always ends in the listener's file name.
    lines = bridge.paste_lines(pathlib.Path(r"C:\Users\a b\AppData\Local\md-mcp\md_mcp_listener.py"))
    assert ast.parse(lines).body[0].value.value.endswith(r"md-mcp\md_mcp_listener.py")
