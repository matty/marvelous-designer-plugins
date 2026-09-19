"""Fixtures for the tests that need a live Marvelous Designer.

They talk to MD over the bridge, so MD must be open with ``plugin/md_mcp_listener.py``
running in it. Without that they skip with instructions rather than failing: an absent
listener means the setup has not been done, not that MD misbehaved.

**They build into the open scene and call ``NewProject``, which discards it.** MD has no
undo, so the fixture refuses to run against a scene that has anything in it.

Selected with ``pytest -m md_required``; deselected by default.
"""

from __future__ import annotations

import pytest

from md_mcp import bridge


@pytest.fixture(scope="session")
def md() -> dict:
    """The live MD, proven to answer and proven empty before anything is built in it."""
    try:
        reply = bridge.ping()
        open_pieces = bridge.scene().get("pattern_count", 0)
    except bridge.BridgeError as exc:
        pytest.skip(str(exc))

    if open_pieces:
        pytest.skip(
            f"Marvelous Designer has {open_pieces} pattern piece(s) open. These tests "
            "call NewProject, which discards the scene, and MD has no undo. Save and "
            "start an empty project, then re-run."
        )
    return reply


@pytest.fixture(scope="session")
def md_live() -> dict:
    """The live MD, proven only to answer -- for tests that do not touch the scene.

    The ``md`` fixture refuses a non-empty scene because its tests call ``NewProject``.
    That is the right guard for them and the wrong one for a read-only claim: it means
    that whenever there is real work open -- which is whenever anyone is using this for
    anything -- nearly the whole live suite is unrunnable. Tests taking this fixture
    must not build, delete or simulate; they may read, and they may run a script whose
    only effect is on the clock.
    """
    try:
        return bridge.ping()
    except bridge.BridgeError as exc:
        pytest.skip(str(exc))
