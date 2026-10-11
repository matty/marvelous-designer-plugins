"""md_guide: the measured facts the server instructions have no room for."""

from __future__ import annotations

import re

from md_mcp import guide
from md_mcp.server import build_server
from tests.test_server import call, error_text, payload

TOPICS = ["workflow", "readback", "seams", "placement", "saving", "simulation"]


def test_the_guide_ships_with_the_package() -> None:
    # Read through importlib.resources, so this fails if guide.md is not package data.
    assert guide.text().startswith("# How Marvelous Designer behaves")


def test_every_topic_is_there_and_short() -> None:
    sections = guide.sections()

    assert list(sections) == TOPICS
    # Short enough that reading one costs little; the whole guide stays cheap too.
    assert all(len(body) < 1500 for body in sections.values()), {
        topic: len(body) for topic, body in sections.items()
    }
    assert len(guide.text()) < 6000


def test_md_guide_returns_all_of_it_or_one_topic() -> None:
    server = build_server()

    everything = payload(call(server, "md_guide"))
    one = payload(call(server, "md_guide", {"topic": "Placement"}))

    assert everything["topics"] == TOPICS and "## seams" in everything["text"]
    assert one["topic"] == "placement" and "SetArrangementPosition" in one["text"]
    assert "## " not in one["text"]


def test_an_unknown_topic_lists_the_ones_there_are() -> None:
    message = error_text(call(build_server(), "md_guide", {"topic": "draping"}))

    assert "no guide topic 'draping'" in message and "placement" in message


def test_the_instructions_send_the_model_to_the_guide() -> None:
    instructions = build_server().instructions

    assert instructions.startswith("Drives a running Marvelous Designer. Call md_guide")
    assert len(instructions) <= 2048


def test_the_guide_designs_no_garments() -> None:
    # md-mcp is about how MD behaves. Drafting and construction advice belongs to
    # whoever is designing, not to the bridge.
    lowered = guide.text().lower()
    for word in ("lining", "collar", "cape", "drafting", "seam allowance"):
        assert word not in lowered, word


def calls_named() -> set[str]:
    """Every MD call the guide names in backticks, without its module: `Simulate(n)`,
    `utility_api.Simulate(n)` and `SetCamViewPoint` all give Simulate or SetCamViewPoint."""
    found = set()
    for quoted in re.findall(r"`([^`]+)`", guide.text()):
        match = re.match(r"^(?:\w+_api\.)?([A-Z][A-Za-z0-9]+)(?:\(|$)", quoted)
        if match:
            found.add(match.group(1))
    return found


def test_the_guide_names_real_looking_calls() -> None:
    names = calls_named()

    assert {"Simulate", "SetArrangementPosition", "ExportZPrj", "MovePatternPoint"} <= names
