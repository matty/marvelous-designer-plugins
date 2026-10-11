"""md_guide's text: measured facts about how MD behaves, read from ``guide.md``.

The server's instructions are capped at 2,048 characters by Claude Code, which is
nowhere near enough for what has been learnt about MD. So the instructions point here,
and this serves ``guide.md`` -- shipped in the package -- whole or one section at a time.
"""

from __future__ import annotations

import importlib.resources
import re


def text() -> str:
    """The whole guide."""
    return importlib.resources.files("md_mcp").joinpath("guide.md").read_text(
        encoding="utf-8"
    )


def sections() -> dict[str, str]:
    """Each ``## topic`` section of the guide, by topic, in order."""
    found = {}
    for part in re.split(r"^## ", text(), flags=re.M)[1:]:
        topic, _, body = part.partition("\n")
        found[topic.strip()] = body.strip()
    return found


def read(topic: str = "") -> dict:
    """The guide, or one topic of it. Raises ValueError, listing the topics, if unknown."""
    available = sections()
    if not topic:
        return {"topics": list(available), "text": text()}
    wanted = topic.strip().lower()
    if wanted not in available:
        raise ValueError(
            f"no guide topic {topic!r}; the topics are {', '.join(available)}, or leave "
            "topic empty for all of it"
        )
    return {"topic": wanted, "topics": list(available), "text": available[wanted]}
