"""The MCP server: Marvelous Designer's whole API, over the live bridge.

Four tools. ``md_status`` says whether MD is reachable, ``md_api`` searches everything
the running build exposes, ``md_call`` calls any of it, and ``run_md_python`` runs a
script inside MD for work that is several calls deep. There is no curated subset and no
wrapper per feature: the API MD ships is the API this serves.

Everything here is a thin shell. The socket is in ``md_mcp.bridge``; the half that runs
inside MD is ``plugin/md_mcp_listener.py``.

Tools **fail** rather than returning an empty result when MD is not listening. A caller
that read "no patterns found" as success would conclude MD is connected and idle, which
is the opposite of true. See ``docs/adr/001-bridge-transport.md``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from md_mcp import __version__, bridge


def _reportable(fn: Callable) -> Callable:
    """Let a tool's own message reach the caller.

    The SDK replaces the text of any exception a tool raises with a generic one, except
    for ``ToolError`` -- everything else is treated as a crash whose detail stays on the
    server. Every error raised below names what the caller got wrong or what to do next,
    so it is worth nothing unless it arrives.

    Only the two deliberate kinds are converted: a rejected argument (``ValueError``) and
    a bridge that could not deliver (``BridgeError``, which already wraps every socket
    error). Anything else from inside md-mcp is a bug, and must keep its traceback and
    reach the caller as a crash -- dressed up as a ToolError it reads like bad input, and
    the model retries it forever.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        try:
            return fn(*args, **kwargs)
        except (ValueError, bridge.BridgeError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


#: What MD reports back about the scene, beyond the pattern pieces themselves. Kept in
#: one place because ``md_status`` and ``run_md_python`` must report the same thing: a
#: caller that saw a seam count from one and not the other would read the silence as
#: zero.
_SCENE_KEYS = (
    "pattern_count",
    "pieces",
    "seam_group_count",
    "avatars",
    "fabrics",
    "readback_errors",
)


def _scene_summary(scene: dict) -> dict:
    """The scene keys MD actually answered, dropping the ones it could not."""
    return {key: scene[key] for key in _SCENE_KEYS if key in scene}


def build_server() -> MCPServer:
    server = MCPServer(
        name="md-mcp",
        version=__version__,
        instructions=(
            "Drives a running Marvelous Designer. md_api searches everything this "
            "build of MD exposes, md_call calls any of it, run_md_python runs a whole "
            "script inside MD. Search before calling: the search gives the exact "
            "spelling and arity, and MD dispatches its overloads on arity. Lengths are "
            "millimetres and there is no undo. MD must be open with "
            "plugin/md_mcp_listener.py running in it (Plugins > Python Editor > Run).\n"
            "MD fails quietly more often than it fails loudly, so read the scene back "
            "rather than trusting a return value. Three that were measured on "
            "2026.0.315: utility_api.Simulate(n) runs n solver steps and returns True "
            "for any n, so Simulate(1) leaves the garment flat -- it needs a few "
            "hundred, and repeated calls continue from where the last left off. A "
            "sewing call naming a missing line or piece returns False; valid indices "
            "can still pair the wrong edges, so check seam_group_count and the scene. "
            "And when a surface will not take a fabric, check it is a pattern piece "
            "before blaming the binding: measured 2026-09-19, setting all four fabrics "
            "to probe colours and then every one of 56 pieces to a single fabric left "
            "some strap-shaped geometry unchanged, because it was not a pattern piece "
            "at all. Recolour the fabric and look; the reported index is not evidence "
            "either way."
        ),
    )

    @server.tool()
    @_reportable
    def md_status() -> dict:
        """Check that Marvelous Designer is reachable, and say which build it is.

        Ask first in a session: every other tool fails without the listener, and this
        says so in one call rather than in the middle of some real work.

        ``plugin_version`` and ``plugin_path`` are the listener that answered, which
        is not necessarily the newest one installed: MD holds a registered plugin's
        path for good.

        Also reports the scene MD currently holds -- pattern pieces, seam groups,
        avatars and fabrics -- all read out of MD rather than remembered. ``pieces``
        and ``seam_group_count`` are worth checking after any build: a sewing call
        returns False for missing indices, and valid indices can still join the wrong
        edges. Inspect the scene as well as the count.
        """
        reply = bridge.ping()
        scene = bridge.scene()
        status = {
            "connected": True,
            "md_version": reply.get("md_version"),
            "port": bridge.port(),
            # Which listener answered, not which one is on disk. MD keeps a registered
            # plugin's path for good and an old install does not stop existing, so an
            # MD serving a version behind is a real and otherwise invisible state --
            # every symptom of it looks like the bridge lying about MD.
            "plugin_version": reply.get("plugin_version"),
            "plugin_path": reply.get("plugin_path"),
        }
        status.update(_scene_summary(scene))
        return status

    @server.tool()
    @_reportable
    def md_api(search: str = "", query: str = "") -> dict:
        """Search everything the running Marvelous Designer exposes.

        search: words that must all appear in the call name, case-insensitive, so
            (``query`` is accepted as the same argument)
            "pattern piece fabric" finds exactly the two calls that name all three.
            Try "Pattern", "Seamline", "Avatar", "Simulate", "Export", "Fabric",
            "Arrangement". Empty lists every call by name, without signatures, as an
            index; pass a term to get the signatures for what it matches.

        Returns each call as "module.Function" with every available signature. Option
        structs include field names and default values. Read out of the live build, not
        from a captured list: MD's published docs have been wrong on several points, and
        the running host is the only ground truth. Feed a name straight to md_call.

        MD's bindings carry signatures and almost no prose, so a signature tells you a
        call's shape and nothing about its meaning. Where a call's behaviour has been
        measured, the result also carries a "note" saying what using it actually does --
        that Simulate returns True for the argument that leaves the garment flat, that
        writing colour side 1 has ended the MD process. Read the note before calling:
        the calls worth warning about are the ones whose signature reads as reassuring.
        """
        # ``query`` is the same argument under the name callers reach for first. An
        # MCP client drops an argument the tool did not declare rather than refusing
        # the call, so a caller who says query= silently searched for "" and got all
        # 688 calls -- ~99KB spent on a synonym. Accepting both is cheaper than being
        # right about which name it should have been.
        term = search or query
        found = bridge.api(term)
        return {"search": term, "count": len(found), "calls": found}

    @server.tool()
    @_reportable
    def md_call(function: str, args: list | None = None) -> dict:
        """Call one Marvelous Designer API function and return what it returned.

        function: "module.Function" as md_api spells it, e.g. "utility_api.Simulate".
            No parentheses.
        args: positional arguments, in order. MD dispatches its overloads on arity and
            argument type, so pass exactly what the signature says and never a
            placeholder None.

        Lengths are millimetres. A few calls hand back objects with no JSON equivalent;
        those arrive as their repr rather than failing the call.
        """
        return {"function": function, "result": bridge.call(function, args or [])}

    @server.tool()
    @_reportable
    def run_md_python(source: str, timeout_seconds: float | None = None) -> dict:
        """Run a Python script inside Marvelous Designer and report what it printed.

        For work that is several calls deep, or needs a loop, a returned index or a
        condition -- drafting a pattern piece and sewing it, say, where each create call
        returns the index the next one needs. `pattern_api`, `utility_api`, `export_api`,
        `import_api` and `fabric_api` are importable in MD.

        timeout_seconds: how long to wait for the reply, default 300. Raise it for a
            long drape or a large export; lower it to probe a call that may hang, which
            is the only way to tell "MD is still building" from "MD is stuck" without
            spending the full default finding out. Whatever this is set to, it abandons
            the *reply* and never the work -- MD runs the script to the end either way,
            so read the scene back before retrying rather than assuming nothing landed.

        Returns the script's own output plus the scene MD ended up with -- pieces, seam
        groups, avatars, fabrics -- read back from MD rather than echoed. Check them:
        a sewing call returns False for missing indices, but can accept the wrong
        pair of valid edges. Check ``seam_group_count`` and inspect the resulting scene.

        An exception comes back with its traceback, and the listener keeps serving
        either way.
        """
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ToolError(
                f"timeout_seconds must be greater than 0, not {timeout_seconds!r}. "
                "Omit it for the 300s default."
            )
        reply = bridge.run_script(
            source, name="<md-mcp tool>", timeout=timeout_seconds
        )
        result = {"output": reply.get("output", "")}
        result.update(_scene_summary(reply))
        return result

    return server


def main() -> None:
    """Console-script entry point: serve over stdio."""
    build_server().run("stdio")


if __name__ == "__main__":
    main()
