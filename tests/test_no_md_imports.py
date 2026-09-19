"""Architectural invariant: md_mcp never imports Marvelous Designer's host modules.

``pattern_api``, ``utility_api`` and friends are injected by MD's embedded
interpreter. Importing one outside MD raises ImportError, which would make md_mcp
unusable on a machine without MD -- including CI, and any machine testing the server
without running Marvelous Designer.

md_mcp *names* those modules constantly, in the snippets it sends to the listener to
run inside MD. Those are string literals and are exempt, which is why this test parses
imports rather than grepping for names.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from md_mcp import MD_HOST_MODULES

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "md_mcp"


def _source_files() -> list[pathlib.Path]:
    return sorted(SRC.rglob("*.py"))


def _imported_module_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # ``from . import x`` has module None; relative imports are always local.
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_source_tree_is_not_empty() -> None:
    # Guards against the invariant test passing vacuously if the layout moves.
    assert _source_files(), f"no Python sources found under {SRC}"


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_no_md_host_module_imports(path: pathlib.Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = _imported_module_names(tree) & MD_HOST_MODULES
    assert not offenders, (
        f"{path.relative_to(SRC)} imports MD host module(s) {sorted(offenders)}. "
        "md_mcp must stay importable without Marvelous Designer -- send the call to the "
        "listener as source text instead."
    )
