"""md_checkpoint and md_restore, against the fake MD.

The fake's .zprj carries its scene, so a checkpoint really round-trips here. What it
cannot show is MD's own: whether ExportZPrj re-points MD's open project at the copy is
unmeasured, so both answers are tested, and the live test settles which one MD gives.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib

import pytest

from md_mcp import bridge, checkpoints
from md_mcp.server import build_server
from tests.fake_md_host import running_listener
from tests.test_server import call, error_text, payload

SQUARE = """
index = pattern_api.CreatePatternWithPoints(
    [(0.0, 0.0, 0), (300.0, 0.0, 0), (300.0, 300.0, 0), (0.0, 300.0, 0)])
pattern_api.SetPatternPieceName(index, {name!r})
"""


@pytest.fixture
def folder(tmp_path, monkeypatch):
    where = tmp_path / "checkpoints"
    monkeypatch.setenv("MD_MCP_CHECKPOINTS", str(where))
    return where


@pytest.fixture
def md(monkeypatch, folder):
    with running_listener(monkeypatch) as host:
        yield host


@pytest.fixture
def server():
    return build_server()


def test_a_checkpoint_saves_the_scene_and_a_record_of_it(md, server, folder) -> None:
    bridge.run_script(SQUARE.format(name="front"))

    saved = payload(call(server, "md_checkpoint", {"label": "before sewing"}))

    path = pathlib.Path(saved["path"])
    assert path.parent == folder and path.is_file()
    assert path.name.endswith("-before-sewing.zprj")
    assert saved["scene"]["pieces"] == [{"index": 0, "name": "front"}]
    record = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert record["label"] == "before sewing" and record["scene"]["pattern_count"] == 1
    assert "warning" not in saved


def test_restoring_puts_the_scene_back_and_says_it_matches(md, server) -> None:
    bridge.run_script(SQUARE.format(name="front"))
    payload(call(server, "md_checkpoint", {"label": "one piece"}))
    bridge.run_script(SQUARE.format(name="mistake"))
    bridge.call("pattern_api.AddSeamlinePairGroup", [0, 1, 1, 3, True, False])

    restored = payload(call(server, "md_restore", {"checkpoint": "one piece"}))

    assert restored["matches_checkpoint"] is True, restored["differences"]
    assert [p["name"] for p in restored["scene"]["pieces"]] == ["front"]
    assert restored["scene"]["seam_group_count"] == 0
    assert md.seams == []


def test_latest_is_the_newest_checkpoint(md, server) -> None:
    bridge.run_script(SQUARE.format(name="a"))
    payload(call(server, "md_checkpoint", {"label": "first"}))
    bridge.run_script(SQUARE.format(name="b"))
    second = payload(call(server, "md_checkpoint", {"label": "second"}))
    bridge.run_script(SQUARE.format(name="c"))

    restored = payload(call(server, "md_restore"))

    assert restored["restored"] == second["path"]
    assert [p["name"] for p in restored["scene"]["pieces"]] == ["a", "b"]


def test_a_restore_that_does_not_match_the_record_says_how(md, server) -> None:
    bridge.run_script(SQUARE.format(name="front"))
    saved = payload(call(server, "md_checkpoint"))
    record_path = pathlib.Path(saved["path"]).with_suffix(".json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["scene"]["seam_group_count"] = 7
    record_path.write_text(json.dumps(record), encoding="utf-8")

    restored = payload(call(server, "md_restore"))

    assert restored["matches_checkpoint"] is False
    assert any("seam_group_count" in line for line in restored["differences"])


def test_a_checkpoint_that_repoints_mds_project_is_a_warning(md, server) -> None:
    # Unmeasured in MD: if saving a copy makes MD treat the copy as its open project,
    # the user's next Save writes over the checkpoint, not their work.
    md.project_path = "C:/work/cape.zprj"
    md.zprj_repoints_project = True

    saved = payload(call(server, "md_checkpoint"))

    assert "C:/work/cape.zprj" in saved["warning"]
    assert saved["project"]["after"]["value"] == saved["path"]


def test_no_file_means_no_checkpoint_and_a_reason_not_to_go_on(md, server) -> None:
    md.exports_write = False

    message = error_text(call(server, "md_checkpoint"))

    assert "No checkpoint was written" in message and "Do not make the change" in message


def test_an_unknown_checkpoint_lists_the_ones_there_are(md, server) -> None:
    payload(call(server, "md_checkpoint", {"label": "real"}))

    message = error_text(call(server, "md_restore", {"checkpoint": "imaginary"}))

    assert "no checkpoint matches 'imaginary'" in message and "real" in message


def test_only_the_newest_are_kept_and_nothing_else_is_touched(folder, monkeypatch) -> None:
    folder.mkdir(parents=True)
    keep_me = folder / "notes.txt"
    keep_me.write_text("mine")
    start = dt.datetime(2026, 10, 11, 12, 0, 0)
    for minute in range(5):
        path = checkpoints.new_path(f"n{minute}", now=start + dt.timedelta(minutes=minute))
        path.write_bytes(b"zprj")
        checkpoints.record(path, f"n{minute}", {"pattern_count": minute})
        os.utime(path, (1000 + minute, 1000 + minute))

    removed = checkpoints.prune(keep=2)

    assert len(removed) == 3
    assert [entry["label"] for entry in checkpoints.listed()] == ["n4", "n3"]
    assert keep_me.read_text() == "mine"
    assert not any(p.suffix == ".json" and "n0" in p.name for p in folder.iterdir())


def test_two_checkpoints_in_the_same_second_get_different_names(folder) -> None:
    now = dt.datetime(2026, 10, 11, 12, 0, 0)
    folder.mkdir(parents=True)
    first = checkpoints.new_path("x", now=now)
    first.write_bytes(b"zprj")

    assert checkpoints.new_path("x", now=now) != first
