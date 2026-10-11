"""Regression coverage for truthful MD execution and scene reporting."""
import sys
import pytest
from md_mcp import bridge
from tests.fake_md_host import running_listener
from tests.test_server import call, payload
from md_mcp.server import build_server

@pytest.fixture
def md(monkeypatch):
    with running_listener(monkeypatch) as host:
        yield host

def test_search_cannot_hide_pieces_from_the_first_visible_run(md):
    bridge.api("PatternCount")
    reply = bridge.run_script("print('first visible run')")
    assert reply["pieces"] == [], "API search consumed the caller's piece list"

def test_successful_script_survives_pattern_count_readback_failure(md, monkeypatch):
    def broken():
        raise RuntimeError("count unavailable")
    monkeypatch.setattr(sys.modules["pattern_api"], "GetPatternCount", broken)
    reply = bridge.run_script("print('COMPLETED')")
    assert reply["execution_succeeded"] is True
    assert reply["output"].strip() == "COMPLETED"
    assert "count unavailable" in reply["readback_errors"]["pattern_count"]
    assert "pattern_count" not in reply
    assert "pieces" not in reply

def test_failed_piece_name_readback_preserves_other_scene_fields(md, monkeypatch):
    bridge.run_script("pattern_api.CreatePatternWithPoints([(0,0,0),(10,0,0),(0,10,0)])")
    def broken(index):
        raise RuntimeError("name unavailable")
    monkeypatch.setattr(sys.modules["pattern_api"], "GetPatternPieceName", broken)
    reply = bridge.run_script("print('COMPLETED')")
    assert reply["execution_succeeded"] is True
    assert reply["pattern_count"] == 1
    assert "pieces" not in reply
    assert "name unavailable" in reply["readback_errors"]["pieces"]
    assert reply["seam_group_count"] == 0

def test_failure_preserves_output_and_partial_scene_at_mcp_boundary(md):
    result = call(build_server(), "run_md_python", {"source":
        "print('STEP_A_DONE'); pattern_api.CreatePatternWithPoints([(0,0,0),(10,0,0),(0,10,0)]); raise RuntimeError('step B failed')"})
    assert result.is_error
    message = result.content[0].text
    assert "STEP_A_DONE" in message, "progress output was discarded"
    assert '"pattern_count": 1' in message, "partial scene was discarded"
    assert "step B failed" in message
    assert "not rolled back" in message
    assert bridge.scene()["pattern_count"] == 1

def test_status_exposes_listener_build(md):
    result = payload(call(build_server(), "md_status"))
    assert result["plugin_build"] == bridge.ping()["plugin_build"]

