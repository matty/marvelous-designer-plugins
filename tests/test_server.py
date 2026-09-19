"""The MCP protocol surface.

The server is spun up in-process and driven through a real MCP client, so these exercise
tool discovery, argument schemas, structured results and error behaviour rather than
calling the tool functions directly. MD is the fake host; the live equivalents are in
``tests/md_required/``.
"""

from __future__ import annotations

import json

import anyio
import pytest
from mcp import Client

from md_mcp import bridge as server_bridge
from md_mcp.server import build_server
from tests.fake_md_host import free_port, running_listener

TOOLS = ["md_status", "md_api", "md_call", "run_md_python"]


@pytest.fixture
def server():
    return build_server()


def call(server, name: str, arguments: dict | None = None):
    async def run():
        async with Client(server) as client:
            return await client.call_tool(name, arguments or {})

    return anyio.run(run)


def tools(server):
    async def run():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    return anyio.run(run)


def payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def error_text(result) -> str:
    assert result.is_error, "expected the call to fail"
    return result.content[0].text


# ------------------------------------------------------------------ discovery


def test_every_tool_is_advertised(server) -> None:
    assert {tool.name for tool in tools(server)} == set(TOOLS)


def test_every_tool_documents_itself(server) -> None:
    for tool in tools(server):
        assert tool.description, f"{tool.name} has no description"


def test_md_call_advertises_its_arguments(server) -> None:
    schema = next(t for t in tools(server) if t.name == "md_call").input_schema

    assert set(schema["properties"]) >= {"function", "args"}
    assert schema["required"] == ["function"]


def test_search_is_optional_so_the_whole_api_can_be_listed(server) -> None:
    schema = next(t for t in tools(server) if t.name == "md_api").input_schema

    assert not schema.get("required")


# --------------------------------------------------------------------- status


def test_status_reports_the_build_and_the_scene(server, monkeypatch) -> None:
    with running_listener(monkeypatch):
        result = payload(call(server, "md_status"))

    assert result["connected"] is True
    assert result["md_version"] == "2026.0.315"
    assert result["pattern_count"] == 0


def test_status_names_the_listener_that_answered(server, monkeypatch) -> None:
    # Not the listener on disk: MD holds a registered plugin's path for good, so an
    # install from two versions ago keeps answering, and every symptom of that reads
    # as md-mcp getting MD wrong. The path is the only thing that tells them apart.
    with running_listener(monkeypatch):
        result = payload(call(server, "md_status"))

    assert result["plugin_version"] == "0.0.0+source"
    assert result["plugin_path"].endswith("md_mcp_listener.py")


def test_status_fails_loudly_when_md_is_not_listening(server, monkeypatch) -> None:
    # It must fail, not report connected=False: a caller that reads a returned object as
    # success would carry on and blame MD for the next tool's error instead.
    monkeypatch.setenv("MD_MCP_PORT", str(free_port()))

    message = error_text(call(server, "md_status"))

    assert "md_mcp_listener.py" in message
    # The menu path as 2026.0.315 actually spells it. "Script > Python" is what the
    # published docs say and there is no such menu in this build; sending someone
    # looking for it is the difference between a two-minute fix and a lost afternoon.
    assert "Plugins > Python Editor" in message


# ------------------------------------------------------------------ MD's API


def test_md_api_searches_the_live_build(server, monkeypatch) -> None:
    with running_listener(monkeypatch):
        result = payload(call(server, "md_api", {"search": "PatternCount"}))

    assert result["count"] == 1
    assert result["calls"][0]["call"] == "pattern_api.GetPatternCount"


def test_md_call_reaches_any_function_md_exposes(server, monkeypatch) -> None:
    with running_listener(monkeypatch):
        call(
            server,
            "md_call",
            {
                "function": "pattern_api.CreatePatternWithPoints",
                "args": [[[0, 0, 0], [100, 0, 0], [100, 100, 0]]],
            },
        )

        result = payload(
            call(server, "md_call", {"function": "pattern_api.GetPatternCount"})
        )

    assert result["result"] == 1


def test_md_call_refuses_anything_outside_mds_modules(server, monkeypatch) -> None:
    with running_listener(monkeypatch) as md:
        message = error_text(
            call(server, "md_call", {"function": "subprocess.run", "args": ["calc"]})
        )

    assert "unknown Marvelous Designer module" in message
    assert md.calls == []


def test_run_md_python_returns_what_the_script_printed(server, monkeypatch) -> None:
    with running_listener(monkeypatch):
        result = payload(
            call(
                server,
                "run_md_python",
                {"source": "import pattern_api\nprint(pattern_api.GetPatternCount())"},
            )
        )

    assert result["output"].strip() == "0"
    assert result["pattern_count"] == 0


def test_missing_seam_readback_does_not_report_a_successful_script_as_failed(
    server, monkeypatch
) -> None:
    import sys

    with running_listener(monkeypatch):
        monkeypatch.delattr(sys.modules["pattern_api"], "GetSeamlinePairGroupCount")

        result = payload(
            call(
                server,
                "run_md_python",
                {
                    "source": (
                        "import pattern_api\n"
                        "print(pattern_api.CreatePatternWithPoints("
                        "[[0, 0, 0], [100, 0, 0], [100, 100, 0]]))"
                    )
                },
            )
        )

    assert result["output"].strip() == "0"
    assert result["pattern_count"] == 1
    assert "seam_group_count" not in result
    assert "GetSeamlinePairGroupCount" in result["readback_errors"]["seam_group_count"]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("raise RuntimeError('no avatar')", "no avatar"),
        ("import sys; sys.exit(0)", "SystemExit: 0"),
    ],
)
def test_a_failing_script_reports_why_and_keeps_the_server_usable(
    server, monkeypatch, source, message
) -> None:
    with running_listener(monkeypatch):
        error = error_text(call(server, "run_md_python", {"source": source}))

        assert message in error
        assert payload(call(server, "md_status"))["connected"] is True


def test_md_api_accepts_query_as_a_name_for_search(server, monkeypatch) -> None:
    # An MCP client drops an argument the tool did not declare rather than refusing the
    # call, so a caller reaching for the obvious name searched for "" instead and got
    # the whole build back -- ~99KB spent on a synonym, twice in one session.
    with running_listener(monkeypatch):
        by_query = payload(call(server, "md_api", {"query": "PatternCount"}))
        by_search = payload(call(server, "md_api", {"search": "PatternCount"}))

    assert by_query["count"] == 1
    assert by_query["calls"] == by_search["calls"]
    assert by_query["search"] == "PatternCount"


def test_run_md_python_advertises_a_timeout(server) -> None:
    schema = next(t for t in tools(server) if t.name == "run_md_python").input_schema
    assert set(schema["properties"]) >= {"source", "timeout_seconds"}
    assert "source" in schema.get("required", [])
    assert "timeout_seconds" not in schema.get("required", [])


def test_run_md_python_hands_its_deadline_to_the_bridge(server, monkeypatch) -> None:
    # Without this the caller cannot fail fast on a call that has hung, and cannot wait
    # longer than 300s for a build that is genuinely slow.
    seen: dict = {}
    real = server_bridge.run_script

    def spy(source, name=None, timeout=None):
        seen["timeout"] = timeout
        return real(source, name=name)

    with running_listener(monkeypatch):
        monkeypatch.setattr(server_bridge, "run_script", spy)
        call(server, "run_md_python", {"source": "print(1)", "timeout_seconds": 12.5})

    assert seen["timeout"] == 12.5


def test_a_nonsense_deadline_is_refused_rather_than_passed_to_a_socket(server) -> None:
    # A zero or negative timeout reaches socket.create_connection as a non-blocking
    # connect and fails with an errno that says nothing about what the caller got wrong.
    result = call(server, "run_md_python", {"source": "print(1)", "timeout_seconds": 0})
    assert result.is_error
    assert "greater than 0" in str(result.content[0].text)
