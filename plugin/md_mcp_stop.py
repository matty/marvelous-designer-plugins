"""md-mcp: Stop MCP — stop the bridge this Marvelous Designer session is serving.

The Plugin tab entry beside Start MCP, which runs ``md_mcp_listener.py``. Clicking it
kills the listener's timer and closes its port; MD carries on as it was. Start MCP opens
it again.

It does not import the listener -- running that file is what Start does. It finds the
running listener where the listener leaves itself, on ``builtins``: MD gives every run a
fresh namespace, and that is the one place two runs share.
"""

import builtins

import utility_api

#: Must match ``_REGISTRY`` in md_mcp_listener.py.
_REGISTRY = "__md_mcp_listeners__"


def stop_all():
    """Stop every listener this MD process is serving. Returns the ports stopped."""
    table = getattr(builtins, _REGISTRY, None) or {}
    stopped = []
    for port, state in sorted(table.items(), key=lambda item: str(item[0])):
        halt = state.get("halt") if isinstance(state, dict) else None
        if callable(halt) and halt("Stop MCP was clicked"):
            stopped.append(port)
    return stopped


def main():
    stopped = stop_all()
    if stopped:
        message = "md-mcp: stopped listening on port {}.".format(
            ", ".join(str(port) for port in stopped)
        )
    else:
        message = "md-mcp: nothing to stop -- the bridge was not listening."
    print(message)
    # Where a clicked plugin's print output goes has not been measured, so the outcome
    # goes in a message box too.
    box = getattr(
        utility_api, "DisplayMessageBoxW", getattr(utility_api, "DisplayMessageBox", None)
    )
    if box is not None:
        try:
            box(message)
        except Exception:
            pass


#: Runs unless something that loads it deliberately says not to -- see the same rule,
#: and why it is inverted, at the foot of md_mcp_listener.py.
if globals().get("MD_MCP_AUTORUN", True):
    main()
