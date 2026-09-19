"""md-mcp — an MCP server for a running Marvelous Designer.

Two halves. This package speaks MCP and holds one end of a loopback socket
(``md_mcp.bridge``); ``plugin/md_mcp_listener.py`` runs inside MD and holds the other.
Everything MD exposes is reachable through them, and nothing here wraps it.

``md_mcp`` itself never imports MD's host modules, so it installs and tests on a machine
with no Marvelous Designer. ``tests/test_no_md_imports.py`` enforces that.
"""

__version__ = "0.3.0"

#: Modules injected by Marvelous Designer's embedded interpreter. Importing any of
#: these outside MD raises ImportError, so no module in this package may do it.
MD_HOST_MODULES = frozenset(
    {
        "pattern_api",
        "utility_api",
        "import_api",
        "export_api",
        "fabric_api",
        "ApiTypes",
    }
)
