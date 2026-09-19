"""The live bridge, both ends, with a fake Marvelous Designer in the middle.

The listener under test is the file that ships, executed with fake host modules; the
client under test is ``md_mcp.bridge``. What is not covered here is MD's own behaviour
-- see ``tests/md_required/test_bridge_live.py``.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from md_mcp import bridge
from tests.fake_md_host import free_port, running_listener, stop_listener

BUILD_A_SQUARE = """
import pattern_api
index = pattern_api.CreatePatternWithPoints(
    [(0.0, 0.0, 0), (300.0, 0.0, 0), (300.0, 300.0, 0), (0.0, 300.0, 0)]
)
pattern_api.SetPatternPieceName(index, "square")
print(index)
"""


@pytest.fixture
def md(monkeypatch):
    with running_listener(monkeypatch) as host:
        yield host


def test_ping_reports_the_md_version(md) -> None:
    assert bridge.ping()["md_version"] == "2026.0.315"


def test_scene_is_empty_before_anything_is_built(md) -> None:
    assert bridge.scene() == {
        "ok": True,
        "pattern_count": 0,
        "pieces": [],
        "seam_group_count": 0,
        "avatars": [],
        "fabrics": ["FABRIC 1"],
    }


def test_the_scene_counts_the_seams_md_actually_made(md) -> None:
    # Count the seam groups actually present after sewing, independently of the
    # sewing call's return value. The count alone cannot prove the right edges joined.
    bridge.run_script(BUILD_A_SQUARE)
    assert bridge.scene()["seam_group_count"] == 0

    bridge.call("pattern_api.AddSeamlinePairGroup", [0, 1, 0, 3, True, False])

    assert bridge.scene()["seam_group_count"] == 1


def test_the_scene_does_not_count_fabrics_with_the_call_that_lies(md) -> None:
    # fabric_api.GetFabricCount() answers 0 for a scene that plainly has a fabric in it
    # (measured on 2026.0.315, and the fake answers 0 for the same reason). Counting
    # with it would report every scene as having no fabric.
    assert bridge.scene()["fabrics"] == ["FABRIC 1"]


def test_a_readback_md_cannot_answer_does_not_fail_the_build(md, monkeypatch) -> None:
    # A build that succeeded must not be reported as a failed request because one
    # readback call is missing from this build of MD. The error is reported in its
    # place, rather than a 0 that would read as "nothing was sewn".
    import sys

    def boom() -> int:
        raise AttributeError("no such call in this build")

    monkeypatch.setattr(sys.modules["pattern_api"], "GetSeamlinePairGroupCount", boom)

    scene = bridge.scene()

    assert scene["pattern_count"] == 0
    assert "seam_group_count" not in scene
    assert "no such call" in scene["readback_errors"]["seam_group_count"]


def test_running_a_script_reports_what_md_ended_up_with(md) -> None:
    reply = bridge.run_script(BUILD_A_SQUARE, name="square.py")

    assert reply["output"].strip() == "0"  # the script's own print
    assert [piece["name"] for piece in reply["pieces"]] == ["square"]
    assert ("CreatePatternWithPoints", 4) in md.calls


def test_a_script_that_raises_reports_the_error_and_the_listener_survives(md) -> None:
    with pytest.raises(bridge.BridgeError) as failure:
        bridge.run_script("raise ValueError('seam 3 is nonsense')")

    assert "seam 3 is nonsense" in str(failure.value)
    assert "ValueError" in str(failure.value)  # the traceback comes back too
    assert bridge.ping()["md_version"] == "2026.0.315"


def test_an_oserror_from_the_script_is_an_answer_not_a_dropped_connection(md) -> None:
    # Exporting to a locked or missing path is the everyday case, and it raises OSError
    # inside MD. Caught as "the peer went away" it would close the connection silently
    # and the user would restart MD over a bridge that never broke.
    with pytest.raises(bridge.BridgeError) as failure:
        bridge.run_script("open('Z:/nowhere/out.obj', 'w')")

    assert "closed the connection" not in str(failure.value)
    assert "Z:" in str(failure.value)
    assert bridge.ping()["ok"]


def test_a_run_with_no_source_is_refused(md) -> None:
    with pytest.raises(bridge.BridgeError, match="source"):
        bridge.run_script("")


def test_an_unknown_op_is_refused(md) -> None:
    with pytest.raises(bridge.BridgeError, match="unknown op"):
        bridge.request("simulate")


def test_the_listener_keeps_serving_after_the_entry_point_returned(md) -> None:
    # The whole transport rests on this: MD runs the script once and returns to its UI.
    for _ in range(3):
        assert bridge.ping()["ok"]


def test_a_connection_that_says_nothing_does_not_hold_the_bridge(md) -> None:
    # The accept loop is serial, so a peer that connects and goes quiet -- a port
    # scanner, a stray browser tab -- must be dropped rather than served.
    silent = socket.create_connection(("127.0.0.1", bridge.port()), timeout=2.0)
    try:
        assert bridge.ping()["ok"]
    finally:
        silent.close()


def test_only_one_request_at_a_time_runs_inside_md(md) -> None:
    # Clients are concurrent; MD must not be. Two scripts that each bracket a pause
    # between two calls would interleave if anything served them in parallel, and MD is
    # not re-entrant. The loop is serial by construction -- one thread, one request at a
    # time -- and this is what that buys.
    slow = (
        "import pattern_api, time\n"
        "pattern_api.SetPatternLayer(0, {0!r} + '-in')\n"
        "time.sleep(0.2)\n"
        "pattern_api.SetPatternLayer(0, {0!r} + '-out')\n"
    )
    done = []

    def run(tag: str) -> None:
        bridge.run_script(slow.format(tag), name=tag)
        done.append(tag)

    threads = [threading.Thread(target=run, args=(tag,)) for tag in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert sorted(done) == ["a", "b"]
    names = [call[2] for call in md.calls if call[0] == "SetPatternLayer"]
    assert names in (
        ["a-in", "a-out", "b-in", "b-out"],
        ["b-in", "b-out", "a-in", "a-out"],
    ), names


def test_a_shutdown_waits_for_the_build_in_front_of_it(md) -> None:
    # One thread, one request at a time: a shutdown arriving mid-build is answered when
    # the build ends, not before. Worth pinning down because it is the price of serving
    # on MD's main thread, and because the thing that used to need a prompt shutdown --
    # re-running the listener -- no longer goes through the socket at all
    # (test_a_second_run_stops_the_listener_without_the_socket).
    building = threading.Thread(
        target=bridge.run_script, args=("import time\ntime.sleep(0.8)",), daemon=True
    )
    building.start()
    time.sleep(0.2)

    began = time.monotonic()
    assert bridge.request("shutdown", timeout=10.0)["stopped"]
    answered_in = time.monotonic() - began

    assert answered_in >= 0.4, f"shutdown answered in {answered_in:.1f}s, before the build"
    building.join(10)


def test_a_second_run_stops_the_listener_without_the_socket(monkeypatch) -> None:
    # Inside MD the second run executes *nested inside* the first one's serve loop, so
    # the first cannot answer a shutdown, or let go of its port, until the second
    # returns. Anything that waits on the socket here deadlocks. Setting a flag the
    # running loop will see when it resumes is the only order that works, so start()
    # must stop rather than replace, and must say so rather than appear to have bound.
    shared: dict = {}

    with running_listener(monkeypatch, namespace=shared):
        port = bridge.port()
        assert bridge.ping()["ok"]

        assert shared["start"](block=False) is None, "the second run bound a port"

        for _ in range(100):  # it stops when the loop next comes round
            if _port_closed(port):
                break
            time.sleep(0.05)
        else:
            pytest.fail("the listener kept serving after a second run")


def test_a_third_run_starts_a_fresh_listener(monkeypatch) -> None:
    # The other half of that contract: once the stopped one has let go, running it
    # again must serve. Without this, "run it once more" is advice nobody has checked.
    shared: dict = {}

    with running_listener(monkeypatch, namespace=shared):
        port = bridge.port()
        shared["start"](block=False)
        for _ in range(100):
            if _port_closed(port):
                break
            time.sleep(0.05)

        shared["start"](block=False)
        try:
            assert bridge.ping()["md_version"] == "2026.0.315"
        finally:
            stop_listener(port)


def _port_closed(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
    except OSError:
        return True
    return False


def test_no_listener_says_how_to_start_one(monkeypatch) -> None:
    monkeypatch.setenv("MD_MCP_PORT", str(free_port()))

    with pytest.raises(bridge.BridgeError) as failure:
        bridge.scene()

    assert "md_mcp_listener.py" in str(failure.value)


def test_something_else_on_the_port_is_not_mistaken_for_md(monkeypatch) -> None:
    port = free_port()
    monkeypatch.setenv("MD_MCP_PORT", str(port))
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.listen(1)

    def serve() -> None:
        connection, _ = listener.accept()
        connection.sendall(b"HTTP/1.1 404 Not Found\r\n\r\n")
        connection.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        with pytest.raises(
            bridge.BridgeError, match="Unreadable reply|went away while"
        ):
            bridge.ping()
    finally:
        listener.close()


def test_valid_json_that_is_not_a_reply_is_reported_not_crashed(monkeypatch) -> None:
    # A bare array is valid JSON, and any line-oriented service could send one. Treated
    # as a reply object it raises AttributeError, which escapes as an opaque crash
    # instead of the message naming the port.
    port = free_port()
    monkeypatch.setenv("MD_MCP_PORT", str(port))
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.listen(1)

    def serve() -> None:
        connection, _ = listener.accept()
        connection.recv(65536)
        connection.sendall(b"[1, 2, 3]\n")
        connection.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        with pytest.raises(bridge.BridgeError, match="not an md-mcp reply"):
            bridge.ping()
    finally:
        listener.close()


# ------------------------------------------------------------- MD's whole API


def test_api_search_finds_a_call_and_its_module(md) -> None:
    found = bridge.api("PatternCount")

    assert [entry["call"] for entry in found] == ["pattern_api.GetPatternCount"]


def test_api_search_is_case_insensitive_and_skips_private_members(md) -> None:
    assert [entry["call"] for entry in bridge.api("patterncount")] == [
        "pattern_api.GetPatternCount"
    ]
    assert not [entry for entry in bridge.api("") if "._" in entry["call"]]


def test_calling_an_md_function_returns_its_value(md) -> None:
    assert bridge.call("pattern_api.GetPatternCount") == 0

    bridge.call("pattern_api.CreatePatternWithPoints", [[[0, 0, 0], [1, 0, 0]]])

    assert bridge.call("pattern_api.GetPatternCount") == 1


def test_calling_passes_arguments_in_order(md) -> None:
    bridge.call("pattern_api.CreatePatternWithPoints", [[[0, 0, 0]]])

    bridge.call("pattern_api.SetPatternPieceName", [0, "yoke"])

    assert bridge.call("pattern_api.GetPatternPieceName", [0]) == "yoke"


def test_a_call_outside_mds_modules_is_refused_before_it_reaches_md(md) -> None:
    with pytest.raises(ValueError, match="unknown Marvelous Designer module"):
        bridge.call("os.remove", ["C:/important.zprj"])

    with pytest.raises(ValueError, match="module.Function"):
        bridge.call("Simulate")

    assert md.calls == []


def test_a_call_that_needs_an_md_object_says_md_call_cannot_reach_it(md) -> None:
    # 37 calls in this build take an ApiTypes struct, which has no JSON form -- so no
    # arguments md_call can express will ever work. pybind's raw complaint reads as
    # "wrong types", and the obvious next move is another guess at the types. It has to
    # say that guessing is hopeless, and name the way through.
    with pytest.raises(bridge.BridgeError) as failure:
        bridge.call("import_api.ImportZprj", ["C:/x.zprj"])

    message = str(failure.value)
    assert "ImportZPRJOption" in message
    assert "no JSON form" in message
    assert "run_md_python" in message


def test_an_arity_mistake_is_told_apart_from_that_and_points_at_the_search(md) -> None:
    # The same pybind TypeError, and a different problem: this one *is* fixable from
    # md_call, so the advice must be to read the signatures rather than to give up on
    # the tool.
    with pytest.raises(bridge.BridgeError) as failure:
        bridge.call("utility_api.SetSimulationQuality", [1])

    message = str(failure.value)
    assert "md_api" in message
    assert "no JSON form" not in message
    # pybind's own text survives: it lists the overloads that would have worked.
    assert "arg0: int, arg1: int" in message


def test_a_function_name_cannot_smuggle_code_past_the_module_check(md) -> None:
    # The name is pasted into generated source, so a payload needs no dot to escape the
    # module whitelist -- it closes the call and appends its own statement.
    payload = 'pattern_api.GetPatternCount()}, default=repr));import os;os.getcwd();#'

    with pytest.raises(ValueError, match="no parentheses"):
        bridge.call(payload)

    with pytest.raises(ValueError, match="no parentheses"):
        bridge.call("pattern_api.GetPatternCount()")  # the everyday version

    assert md.calls == []


def _dies_after_reading(port: int, *, reset: bool) -> socket.socket:
    """A listener that accepts, reads the request and dies without answering.

    Both ways a crashing MD can end the connection: ``reset`` sends an RST by closing
    with SO_LINGER at zero, which is what MD 2026.0.315 actually did when it went down
    mid-call; the other path closes cleanly and reaches the client as end-of-file.
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)

    def serve() -> None:
        connection, _ = listener.accept()
        connection.recv(65536)
        if reset:
            connection.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
        connection.close()

    threading.Thread(target=serve, daemon=True).start()
    return listener


@pytest.mark.parametrize("reset", [True, False], ids=["reset", "clean-close"])
def test_md_dying_mid_call_is_reported_as_a_crash_not_a_port_problem(
    monkeypatch, reset: bool
) -> None:
    # The measured case: MD exits while running the call, taking the listener with it.
    # Reported as "either MD closed, or something else holds that port", it reads as a
    # configuration problem, and the thing that actually happened -- the scene is gone
    # -- goes unsaid.
    port = free_port()
    monkeypatch.setenv("MD_MCP_PORT", str(port))
    listener = _dies_after_reading(port, reset=reset)
    try:
        with pytest.raises(bridge.BridgeError) as failure:
            bridge.scene()
    finally:
        listener.close()

    message = str(failure.value)
    assert "went away while it was running 'scene'" in message
    assert "unsaved" in message
    assert "crashed" in message
    # It must not send the reader after the port, which is what the old wording did.
    assert "holds that port" not in message


def test_api_search_requires_every_word_not_the_whole_phrase(md) -> None:
    # A search anybody would type. Matched as one substring it is inside no name in the
    # build, so it answers nothing -- which reads as "MD has no such call".
    assert [e["call"] for e in bridge.api("pattern count")] == [
        "pattern_api.GetPatternCount"
    ]
    assert bridge.api("count pattern") == bridge.api("pattern count")


def test_empty_api_search_lists_names_without_signatures(md) -> None:
    # An empty search is what a mistyped argument name silently produces, and every
    # signature of the whole build is ~99KB of context spent on a typo.
    everything = bridge.api("")
    assert everything, "an empty search should still list the build"
    assert all(set(entry) == {"call"} for entry in everything)
    # That a *term* search still carries signatures needs a real build: the fake host's
    # functions have no pybind docstrings, so nothing has signatures here either way.
    # tests/md_required/test_bridge_live.py covers it.
    assert [e["call"] for e in bridge.api("patterncount")] == [
        "pattern_api.GetPatternCount"
    ]


def test_run_does_not_repeat_an_unchanged_piece_list(md) -> None:
    # Measured on a 58-piece garment: a run whose output was two characters returned
    # 2,770, of which 2,500 were the piece list from the reply before it.
    first = bridge.run_script(BUILD_A_SQUARE)
    assert isinstance(first["pieces"], list)

    second = bridge.run_script("print('nothing changed')")
    assert second["pieces_unchanged"] is True
    assert not isinstance(second["pieces"], list)
    # The numbers worth checking are scalars and must survive.
    assert second["pattern_count"] == first["pattern_count"]
    assert "seam_group_count" in second


def test_a_changed_piece_list_is_always_sent_in_full(md) -> None:
    # Suppression must never be able to hide a change: that would turn the scene
    # read-back into a thing that is only sometimes true.
    bridge.run_script(BUILD_A_SQUARE)
    bridge.run_script("print('settle')")
    grown = bridge.run_script(BUILD_A_SQUARE)
    assert isinstance(grown["pieces"], list)
    assert not grown.get("pieces_unchanged")


def test_md_modules_are_bound_before_a_script_runs(md) -> None:
    # MD's own Python Editor injects these, so scripts written for MD -- and the habits
    # of anyone who has used it -- assume they are already there.
    reply = bridge.run_script("print(pattern_api.GetPatternCount())")
    assert reply["output"].strip() == "0"


def test_a_timeout_says_the_work_carries_on_without_you(md) -> None:
    # A timeout abandons the reply, not the script: MD runs it to the end and the
    # changes land. Retrying on the assumption that nothing happened is how a build
    # gets applied twice.
    with pytest.raises(bridge.BridgeError) as failure:
        bridge.request("run", timeout=0.4, source="import time; time.sleep(2.5)")

    message = str(failure.value)
    assert "did not answer" in message
    assert "twice" in message and "changes land" in message


def test_a_failure_names_the_listener_build_that_raised_it(md) -> None:
    # A traceback quoting md_mcp_listener.py is formatted against the file on disk, so
    # once that file is edited under a still-running listener the line numbers are the
    # instance's and the source lines beside them are the new file's. The pair points
    # at unrelated code and reads as a bug in the listener rather than as "you are
    # talking to the old one".
    with pytest.raises(bridge.BridgeError) as failure:
        bridge.run_script("1 / 0")

    message = str(failure.value)
    assert "division by zero" in message
    assert "raised by md-mcp listener" in message
    assert "Stop and Start it" in message


def test_a_run_can_be_given_its_own_deadline(md) -> None:
    # RUN_TIMEOUT is 300s: the right default for a drape, the wrong one for a call that
    # has hung. A bare-path ExportOBJ that never wrote a byte cost five minutes of
    # blocked bridge before "MD is still building" could be told apart from "MD is
    # stuck", and the whole time there was no way to say which kind of work it was.
    started = time.monotonic()
    with pytest.raises(bridge.BridgeError) as failure:
        bridge.run_script("import time; time.sleep(2.5)", timeout=0.4)

    assert time.monotonic() - started < 2.0, "the explicit deadline was not honoured"
    assert "did not answer" in str(failure.value)


def test_a_run_without_a_deadline_still_gets_the_long_default(md, monkeypatch) -> None:
    # The default has to stay generous: a few hundred solver steps legitimately take
    # minutes, and a short default would abandon the reply to a drape that is fine.
    seen: dict = {}
    real = bridge.request

    def spy(op, timeout=bridge.QUERY_TIMEOUT, **payload):
        seen["timeout"] = timeout
        return real(op, timeout=timeout, **payload)

    monkeypatch.setattr(bridge, "request", spy)
    bridge.run_script("print('quick')")
    assert seen["timeout"] == bridge.RUN_TIMEOUT


def test_a_search_result_carries_what_is_known_about_the_call(md) -> None:
    # MD's bindings have signatures and no prose, so without this a model learns that
    # Simulate takes an int and returns a bool -- and nothing about it returning True
    # for the argument that leaves the garment flat.
    entry = next(
        c for c in bridge.api("AddSeamlinePairGroup")
        if c["call"] == "pattern_api.AddSeamlinePairGroup"
    )

    assert "validates indices and not meaning" in entry["note"]
    assert entry["call"] == "pattern_api.AddSeamlinePairGroup"


def test_a_note_is_added_beside_what_md_reported_not_instead_of_it(md) -> None:
    # Annotating must not drop or overwrite anything the live build said. The fake host's
    # functions carry no pybind signature line, so prove it on the shape directly.
    from md_mcp import notes

    entry = {"call": "utility_api.Simulate", "signatures": ["Simulate(arg0: int) -> bool"]}
    untouched = {"call": "utility_api.NothingMeasuredHere", "signatures": ["X() -> None"]}

    notes.annotate([entry, untouched])

    assert entry["signatures"] == ["Simulate(arg0: int) -> bool"]
    assert "solver steps" in entry["note"]
    assert untouched == {"call": "utility_api.NothingMeasuredHere",
                         "signatures": ["X() -> None"]}


def test_a_call_with_nothing_measured_is_returned_untouched(md) -> None:
    # The notes annotate the live build; they never curate it. A call nobody has
    # measured still comes back, just without a note.
    plain = [c for c in bridge.api("Pattern") if "note" not in c]
    assert plain, "every result was annotated, which means the search was too narrow"
    assert all("call" in c for c in plain)
