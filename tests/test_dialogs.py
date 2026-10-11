"""Modal dialogs in MD, seen from md-mcp's side.

The window check itself is Win32 and was measured against MD's real "abnormal seamlines"
dialog on 2026-10-11 (see md_mcp.dialogs). Here it is replaced by a list the test
controls, which proves what md-mcp does with what it sees -- giving up on a blocked
reply early, and saying so after a reply that "succeeded" into a dialog.
"""

from __future__ import annotations

import time

import pytest

from md_mcp import bridge, dialogs
from md_mcp.server import build_server
from tests.fake_md_host import running_listener
from tests.test_server import call, payload

DIALOG = [{"title": "Marvelous Designer Enterprise Offline Annual",
           "class": "Qt6103QWindow", "owner": "Marvelous Designer Enterprise Offline Annual",
           "pid": 1234}]


@pytest.fixture
def md(monkeypatch):
    monkeypatch.setattr(bridge, "DIALOG_POLL", 0.1)
    monkeypatch.setattr(bridge, "DIALOG_SETTLE", 0.0)
    with running_listener(monkeypatch) as host:
        yield host


def show(monkeypatch, *answers):
    """Make dialogs.showing() answer these in turn, then keep answering the last."""
    sequence = list(answers)

    def showing():
        return sequence.pop(0) if len(sequence) > 1 else sequence[0]

    monkeypatch.setattr(dialogs, "showing", showing)


def test_a_dialog_ends_the_wait_in_seconds_not_after_the_timeout(md, monkeypatch) -> None:
    show(monkeypatch, DIALOG)
    started = time.monotonic()

    with pytest.raises(bridge.BridgeError) as failure:
        bridge.run_script("import time\ntime.sleep(3)", timeout=60)

    assert time.monotonic() - started < 2.5
    message = str(failure.value)
    assert "modal dialog" in message and "Qt6103QWindow" in message
    assert "still inside MD" in message
    # The work carried on, as the message says. Wait for it before the next test: the
    # fake runs every listener in one process, where two scripts capturing stdout at
    # once would swap each other's output.
    show(monkeypatch, [])
    assert bridge.request("ping", timeout=10)["ok"]


def test_a_dialog_seen_once_does_not_end_the_wait(md, monkeypatch) -> None:
    # One look is not enough: a progress dialog that flashes up must not abandon a
    # reply that is about to arrive.
    show(monkeypatch, DIALOG, [], [])

    reply = bridge.run_script("import time\ntime.sleep(0.5)\nprint('done')", timeout=30)

    assert reply["output"].strip() == "done"


def test_a_script_that_succeeds_into_a_dialog_says_so(md, monkeypatch) -> None:
    # The cape session: MD showed a meshing error after the script reported success.
    show(monkeypatch, DIALOG)

    result = payload(call(build_server(), "run_md_python", {"source": "print('ok')"}))

    assert result["execution_succeeded"] is True
    assert "modal dialog" in result["dialog_warning"]
    assert result["dialogs"] == DIALOG


def test_md_call_says_so_too(md, monkeypatch) -> None:
    show(monkeypatch, DIALOG)

    result = payload(call(build_server(), "md_call",
                          {"function": "pattern_api.GetPatternCount"}))

    assert result["result"] == 0 and "dialog_warning" in result


def test_no_dialog_adds_nothing(md, monkeypatch) -> None:
    show(monkeypatch, [])

    result = payload(call(build_server(), "run_md_python", {"source": "print('ok')"}))

    assert "dialog_warning" not in result and "dialogs" not in result


def test_md_status_reports_what_md_is_showing(md, monkeypatch) -> None:
    show(monkeypatch, DIALOG)

    assert payload(call(build_server(), "md_status"))["dialogs"] == DIALOG


def test_off_windows_there_is_nothing_to_look_at(monkeypatch) -> None:
    monkeypatch.setattr(dialogs.sys, "platform", "linux")

    assert dialogs.showing() == []
