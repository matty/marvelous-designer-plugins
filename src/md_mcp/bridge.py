"""The live bridge: md-mcp's side of the socket Marvelous Designer listens on.

MD cannot be dialled into by anything it does not host itself, so the connection is a
listener running inside MD (``plugin/md_mcp_listener.py``) that md-mcp connects to. One
JSON object per line each way, then the connection closes; see the listener's docstring
for the protocol.

A failure here is nearly always "MD is running but the listener was never started", so
every error carries the instruction to start it.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import socket
import time
from typing import Any

from md_mcp import dialogs, notes

HOST = "127.0.0.1"
DEFAULT_PORT = 5121
#: Override on both sides -- md-mcp's environment and MD's -- or neither.
PORT_ENV = "MD_MCP_PORT"

#: Names the listener file explicitly, for an install that is in neither usual place.
PLUGIN_ENV = "MD_MCP_PLUGIN"

#: Where ``plugin/install.ps1`` puts the packaged plugin, relative to %LOCALAPPDATA%.
INSTALLED_PLUGIN = ("md-mcp", "md_mcp_listener.py")

#: Building a jacket is ~50 MD calls; a cold MD can take a while over the first one.
RUN_TIMEOUT = 300.0
QUERY_TIMEOUT = 15.0
#: Walking Public Documents' asset tree from a cold disk is slower than a query.
PRESET_TIMEOUT = 60.0


class BridgeError(RuntimeError):
    """MD could not be reached, or refused what it was asked to do."""


def port() -> int:
    return int(os.environ.get(PORT_ENV, DEFAULT_PORT))


def _listener_candidates():
    """Where the listener might be, best first.

    The checkout beats an install deliberately. Running md-mcp from a checkout means
    somebody is working on it, and pointing them at a copy installed weeks ago is how
    an afternoon goes into a bug that was fixed in the file they are looking at.
    """
    override = os.environ.get(PLUGIN_ENV)
    if override:
        yield pathlib.Path(override)
    yield pathlib.Path(__file__).resolve().parents[2] / "plugin" / "md_mcp_listener.py"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        yield pathlib.Path(local).joinpath(*INSTALLED_PLUGIN)


def listener_script() -> pathlib.Path | None:
    """The listener to run inside MD -- installed, or in the checkout, or neither."""
    for candidate in _listener_candidates():
        if candidate.is_file():
            return candidate
    return None


def how_to_connect() -> str:
    script = listener_script()
    where = str(script) if script else "plugin/md_mcp_listener.py from the md-mcp repo"
    return (
        f"No Marvelous Designer listener on {HOST}:{port()}. In MD, click 'Start MCP' "
        "in the Plugin tab (an older install calls it 'md-mcp'); it shows a message box "
        "only if something went wrong. If there is no such entry yet, run "
        f"{where} via Main Menu > Plugins > Python Editor > Run, which starts it and "
        "adds Start MCP and Stop MCP to the Plugin tab. It serves until Stop MCP is "
        "clicked or MD closes. If MD shows an older md-mcp window instead, that listener "
        "predates Start MCP: press Start in it, or update the plugin."
    )


def _died_mid_request(op: str, exc: OSError | None) -> str:
    """What to say when the connection drops after the request has gone out.

    The socket was accepted, so the listener was alive and serving a moment earlier,
    and then it went away without answering. Both endings arrive here: an abrupt reset
    raises ``OSError`` mid-recv, and a process torn down more politely closes cleanly
    and reads as end-of-file. Neither says anything different about the cause, so they
    say the same thing.

    That cause is overwhelmingly MD itself exiting while running the call. MD's API has
    no crash handling -- a call the host cannot take ends the process, and the listener
    goes with it, because the listener runs inside MD, on MD's main thread.

    The wording this replaces ("Either MD closed, or something that is not the md-mcp
    listener holds that port") sent the reader to look at ports, and said nothing about
    the part that cannot be undone: the scene dies with the process, and MD's API has
    no undo and no autosave of its own. Whatever was built since the last save is gone,
    and knowing that immediately is worth more than knowing which errno arrived.
    """
    detail = f": {exc}" if exc is not None else " without answering"
    return (
        f"Marvelous Designer went away while it was running {op!r}{detail}. The "
        "listener took the connection and then died without replying, so MD most "
        "likely crashed on that call: its API has no crash handling, and a call the "
        "host cannot take ends the process.\n\n"
        "Look at MD before retrying. If it is gone, everything unsaved in that scene "
        "went with it -- reopen the project, start the listener again, and treat that "
        "call as the suspect rather than repeating it. If MD is still open, the "
        "listener was stopped instead: click Start MCP in the Plugin tab."
    )


#: How often a request waiting on MD looks for a modal dialog, and how many looks in a
#: row must find one before the wait is abandoned. A dialog holds MD's main thread, so
#: without this a request waits out its whole timeout -- 300 s for a script.
DIALOG_POLL = 1.0
DIALOG_POLLS = 2

#: How long after a script's reply to look for a dialog it left behind. The cape
#: session's meshing error appeared after the script had reported success.
DIALOG_SETTLE = 0.15


def request(
    op: str, timeout: float = QUERY_TIMEOUT, look_after: bool = False, **payload: Any
) -> dict:
    """Send one request, return the reply. Raises :class:`BridgeError` on any failure.

    While waiting it looks for a modal dialog in MD about once a second, and gives up on
    the reply -- not the work -- once one has been up for two looks. With
    ``look_after`` it looks once more after the reply and adds ``dialogs`` and
    ``dialog_warning`` to it, so a script that "succeeded" into a dialog says so.
    """
    message = json.dumps({"op": op, **payload}) + "\n"
    try:
        client = socket.create_connection((HOST, port()), timeout=timeout)
    except OSError as exc:
        raise BridgeError(how_to_connect()) from exc

    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    seen = 0
    with client:
        client.settimeout(min(timeout, DIALOG_POLL))
        try:
            client.sendall(message.encode("utf-8"))
            while not chunks or b"\n" not in chunks[-1]:
                try:
                    chunk = client.recv(65536)
                except TimeoutError:
                    if time.monotonic() >= deadline:
                        raise
                    found = dialogs.showing()
                    seen = seen + 1 if found else 0
                    if seen >= DIALOG_POLLS:
                        raise BridgeError(
                            dialogs.describe(found)
                            + f" md-mcp stopped waiting for {op!r} because of it; the "
                            "request is still inside MD and finishes once the dialog is "
                            "dismissed, so read the scene back before retrying anything."
                        ) from None
                    continue
                if not chunk:
                    break
                chunks.append(chunk)
        except TimeoutError as exc:
            raise BridgeError(
                f"Marvelous Designer did not answer {op!r} within {timeout:g}s. Look "
                "at MD before retrying, and look for a dialog first: some API calls "
                "raise one (an 'abnormal seamlines' warning on a degenerate seam is "
                "the measured case), and a modal dialog holds MD's main thread, which "
                "is the thread serving this bridge. Nothing gets through until it is "
                "dismissed, and everything resumes when it is. Otherwise MD is most "
                "likely still building.\n\n"
                "This gave up on the reply, not on the work: MD runs the script to the "
                "end and its changes land, so retrying can apply the same build twice "
                "-- measured, a 6s script abandoned after 2s finished, and the bridge "
                "answered normally 4.5s later. Read the scene back before deciding "
                "whether anything still needs doing."
            ) from exc
        except OSError as exc:
            raise BridgeError(_died_mid_request(op, exc)) from exc

    raw = b"".join(chunks).split(b"\n", 1)[0]
    if not raw:
        raise BridgeError(_died_mid_request(op, None))
    try:
        reply = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError(
            f"Unreadable reply to {op!r} from {HOST}:{port()} -- something other than "
            "the md-mcp listener holds that port."
        ) from exc

    # A bare array or number is valid JSON and not a reply. Some other line-oriented
    # service on this port would otherwise crash the tool with an AttributeError
    # instead of saying which port to move.
    if not isinstance(reply, dict):
        raise BridgeError(
            f"Reply to {op!r} from {HOST}:{port()} is not an md-mcp reply -- something "
            "other than the listener holds that port."
        )

    if not reply.get("ok"):
        detail = reply.get("error") or "no reason given"
        trace = reply.get("traceback")
        stamp = reply.get("plugin_build")
        progress = reply.get("output")
        scene_keys = ("pattern_count", "pieces", "seam_group_count", "avatars",
                      "fabrics", "readback_errors")
        partial_scene = {key: reply[key] for key in scene_keys if key in reply}
        recovery = ""
        if reply.get("execution_succeeded") is False:
            recovery = "\nEarlier changes were not rolled back. Inspect the scene before retrying."
        if progress:
            recovery += "\nCaptured output before failure:\n" + progress
        if partial_scene:
            recovery += "\nScene after failure: " + json.dumps(partial_scene)
        # Exports checked before the failure that are worth knowing about: a folder
        # created for one, or a file MD reported and did not write.
        flagged = [
            record for record in reply.get("writes") or []
            if isinstance(record, dict)
            and set(record) - {"call", "target", "written"}
        ]
        if flagged:
            recovery += "\nFiles checked before failure: " + json.dumps(flagged)
        raise BridgeError(
            f"Marvelous Designer refused {op!r}: {detail}"
            + recovery
            + (f"\n{str(trace).strip()}" if trace else "")
            # Which listener raised it. A traceback quoting md_mcp_listener.py is
            # formatted against whatever is on disk now, so after the file is
            # edited under a running listener the line numbers belong to the
            # instance and the source lines to the new file, and the two disagree
            # silently. This is what makes a stale listener visible.
            + (
                f"\n(raised by md-mcp listener "
                f"{reply.get('plugin_version')} build {stamp}; if that is not the "
                "build you just installed, the listener running in MD is still the "
                "old one -- click Stop MCP and then Start MCP in the Plugin tab, which "
                "loads the file afresh.)"
                if stamp
                else ""
            )
        )
    if look_after:
        time.sleep(DIALOG_SETTLE)
        found = dialogs.showing()
        if found:
            reply["dialogs"] = found
            reply["dialog_warning"] = dialogs.describe(found)
    return reply


def ping() -> dict:
    """Check the listener is up. Returns MD's version and process id."""
    return request("ping", timeout=5.0)


def scene() -> dict:
    """The pattern pieces currently open in MD, read back from MD itself."""
    return request("scene")


def run_script(
    source: str, name: str | None = None, timeout: float | None = None
) -> dict:
    """Execute an emitted garment script inside MD and report the resulting scene.

    ``timeout`` replaces :data:`RUN_TIMEOUT` for this one call. The default is long
    because a few hundred solver steps legitimately take minutes, and abandoning the
    reply to a drape that is fine is the worse failure. It is the wrong default for a
    call that has *hung*, though, and from out here the two look identical -- so a
    caller who knows which kind of work it is sending should be able to say so.
    """
    return request(
        "run",
        timeout=RUN_TIMEOUT if timeout is None else timeout,
        look_after=True,
        source=source,
        name=name,
    )


#: The modules MD injects into its interpreter. Everything MD exposes is a member of
#: one of these; there is no other surface.
MD_MODULES = (
    "pattern_api",
    "utility_api",
    "export_api",
    "import_api",
    "fabric_api",
    "ApiTypes",
)


def _printed_reply(
    source: str, timeout: float, look_after: bool = False
) -> tuple[Any, dict]:
    """Run a snippet that prints one JSON document: what it printed, and the reply."""
    reply = request(
        "run", timeout=timeout, look_after=look_after, source=source, name="<md-mcp query>"
    )
    output = reply.get("output", "")
    try:
        return json.loads(output), reply
    except json.JSONDecodeError as exc:
        raise BridgeError(
            f"Expected one JSON document from MD, got: {output[:400]!r}"
        ) from exc


def _printed_json(source: str, timeout: float) -> Any:
    """Run a snippet that prints one JSON document, and return what it printed."""
    return _printed_reply(source, timeout)[0]


def _split(function: str) -> tuple[str, str]:
    module, _, name = function.partition(".")
    # ``name`` is pasted into generated source, so anything but a bare identifier is
    # arbitrary code inside MD -- a payload needs no dot to escape. Checking for a dot
    # is not a check; ``isidentifier`` is. It also catches the everyday version, a
    # caller writing "pattern_api.GetPatternCount()", which would otherwise surface as
    # "'int' object is not callable" from somewhere inside MD.
    if not name or not name.isidentifier():
        raise ValueError(
            f"{function!r} is not a Marvelous Designer call; write it as "
            f"module.Function with no parentheses, e.g. pattern_api.GetPatternCount"
        )
    if module not in MD_MODULES:
        raise ValueError(
            f"unknown Marvelous Designer module {module!r}; MD has: "
            + ", ".join(MD_MODULES)
        )
    return module, name


def call(function: str, args: list | None = None) -> Any:
    """Call one MD API function and return what it returned.

    ``default=repr`` on the way out: a few MD calls hand back objects with no JSON
    equivalent (``map`` from ``GetBoundingBoxOfPattern``, the ApiTypes enums), and a
    readable repr is more use to the caller than a refusal.
    """
    return call_checked(function, args)[0]


def call_checked(
    function: str, args: list | None = None
) -> tuple[Any, list | None, list]:
    """:func:`call`, plus the listener's check of any file the call said it wrote, and
    any modal dialog MD was showing just after it (the third item, empty if none).

    The second item is the reply's ``writes``: one record per ``Export*`` call that
    named or returned a path, saying which of those paths exist. ``None`` means the
    listener in MD predates the check, which is not the same as "nothing was written".
    """
    module, name = _split(function)
    source = (
        f"import json, {module}\n"
        f"_args = json.loads({json.dumps(json.dumps(args or []))})\n"
        f"print(json.dumps({{'result': {module}.{name}(*_args)}}, default=repr))\n"
    )
    try:
        printed, reply = _printed_reply(source, RUN_TIMEOUT, look_after=True)
    except BridgeError as exc:
        raise _explained(function, exc) from exc
    return printed["result"], reply.get("writes"), reply.get("dialogs") or []


#: ``Export*`` calls that write nothing, so are never checked -- mirrors ``NOT_WRITES``
#: in the listener, which is where the check itself happens.
NOT_WRITES = frozenset({"ExportObjectBrowserMaterialsList"})


def is_write_call(function: str) -> bool:
    """Whether the listener checks what ``function`` wrote (``module.Export*``)."""
    module, _, name = function.partition(".")
    return (
        module in ("export_api", "pattern_api")
        and name.startswith("Export")
        and name not in NOT_WRITES
    )


#: pybind's complaint when the arguments do not match any overload. It is also what a
#: call needing an MD object says when handed JSON, which is a different problem with a
#: different fix, so the two are told apart by whether the signature mentions one.
_ARGUMENT_MISMATCH = "incompatible function arguments"


def _explained(function: str, failure: BridgeError) -> BridgeError:
    """Turn an argument mismatch into the reason, when the reason is knowable.

    37 calls in this build take an MD object -- ``ImportExportOption``,
    ``ImportZPRJOption`` and friends -- which has no JSON form, so ``md_call`` cannot
    reach them however the arguments are written. Left as pybind's raw complaint that
    reads as "you passed the wrong types", and the next attempt is another guess at the
    types. It needs to say that no arguments will do, and what to use instead.
    """
    if _ARGUMENT_MISMATCH not in str(failure):
        return failure
    if "Marvelous::" not in str(failure):
        return BridgeError(
            f"{failure}\n\nMD dispatches its overloads on arity and argument type. Use "
            f"md_api to see every signature {function} has, and pass exactly one of "
            "them positionally -- never None as a placeholder, which selects a "
            "different overload."
        )
    wanted = sorted(
        {
            word.split("Marvelous::", 1)[1].strip(" ,)")
            for word in str(failure).split()
            if "Marvelous::" in word
        }
    )
    return BridgeError(
        f"{function} takes an MD object ({', '.join(wanted) or 'an ApiTypes struct'}), "
        "which has no JSON form, so md_call cannot reach it with any arguments. Use "
        "run_md_python and build the object there, e.g.:\n"
        f"    import ApiTypes, {function.split('.')[0]}\n"
        f"    option = ApiTypes.{(wanted or ['Option'])[0]}()\n"
        "    option.<field> = ...   # md_api names the fields and their defaults\n"
        f"    print({function}(..., option))"
    )


#: Runs inside MD. Every signature of every matching member, plus the fields of the
#: option objects those signatures ask for.
#:
#: The first line of a docstring is not enough, which is how this started. pybind11
#: writes ``Foo(*args, **kwargs)`` as the first line of every *overloaded* function and
#: puts the real signatures further down -- so the 77 overloaded calls in this build,
#: ``AddSeamlinePairGroup`` and ``GetLineLength`` among them, described themselves as
#: taking anything at all. Those are exactly the calls where it matters, because MD
#: dispatches its overloads on arity and type.
_API_QUERY = """
import json, re
_modules = {modules}
_terms = {search}
# A pybind signature line: optionally numbered for an overload, and always "-> type".
_SIG = re.compile(r"^\\s*(?:\\d+\\.\\s*)?[A-Za-z_~][\\w~]*\\(.*\\)\\s*->.*$")
_found = []
for _name in _modules:
    try:
        _module = __import__(_name)
    except ImportError:
        continue
    for _member in dir(_module):
        _lowered = _member.lower()
        if _member.startswith('_') or not all(_t in _lowered for _t in _terms):
            continue
        if not _terms:
            # No search at all: the whole build, as an index of names. Every signature
            # of all 688 is ~99KB, and an empty search is what a mistyped argument name
            # silently produces -- so the unasked-for answer is the compact one, and a
            # term brings the signatures back.
            _found.append({{'call': _name + '.' + _member}})
            continue
        _value = getattr(_module, _member)
        _doc = getattr(_value, '__doc__', '') or ''
        _lines = [_l.strip() for _l in _doc.splitlines() if _l.strip()]
        _entry = {{'call': _name + '.' + _member}}
        _sigs = [_l for _l in _lines if _SIG.match(_l)]
        if _sigs:
            _entry['signatures'] = _sigs
        # Whatever the docstring says that is not a signature and not pybind's banner.
        # "Foo(*args, **kwargs)" is dropped too: it has no "->" so it is not caught as a
        # signature, and reporting it as the call's documentation is worse than saying
        # nothing -- it reads as though the call really does take anything.
        _prose = [_l for _l in _lines
                  if not _SIG.match(_l)
                  and not _l.startswith('Overloaded function')
                  and not _l.endswith('(*args, **kwargs)')]
        if _prose:
            _entry['doc'] = _prose[0]
        if not callable(_value):
            # An enum member such as ApiTypes.TEXTURE_MAP_BASE_COLOR. Its repr is what
            # a caller needs to recognise it by.
            _entry['value'] = repr(_value)
        elif isinstance(_value, type):
            # An option struct or an enum type. Constructing it is the only way to read
            # the field names and defaults, and nothing else in MD reports them -- so a
            # caller filling in an ImportExportOption would otherwise be guessing.
            try:
                _instance = _value()
            except Exception:
                _instance = None
            if _instance is None:
                _entry['enum_members'] = [
                    _m for _m in dir(_value) if not _m.startswith('_')
                ]
            else:
                _fields = {{}}
                for _field in dir(_instance):
                    if _field.startswith('_'):
                        continue
                    try:
                        _fields[_field] = repr(getattr(_instance, _field))
                    except Exception:
                        pass
                if _fields:
                    _entry['fields'] = _fields
        _found.append(_entry)
print(json.dumps(_found))
"""


def api(search: str = "") -> list[dict]:
    """Every MD member whose name contains all of ``search``'s words.

    Whitespace separates terms and every term has to appear, so "fabric color"
    finds the colour calls on fabrics. Matched as one string instead, a search
    anybody would type -- "bounding box of pattern" -- is a substring of no name
    in the build and answers nothing, which reads as "MD has no such call"
    rather than "try fewer words". Measured 2026-09-19 against 2026.0.315.

    Read out of the live build rather than from a captured list: MD's published docs
    have been wrong on nine separate points, and `dir()` inside the running host is the
    only ground truth there is.

    Results carry a ``note`` where :mod:`md_mcp.notes` has one. MD's bindings ship
    signatures and no prose, so without it a caller learns a call's shape and nothing
    about its meaning -- and the calls worth warning about are exactly the ones whose
    signature reads as reassuring. The note is added beside what MD reported and never
    in place of it.
    """
    source = _API_QUERY.format(
        modules=json.dumps(list(MD_MODULES)),
        search=json.dumps(search.lower().split()),
    )
    found = _printed_json(source, QUERY_TIMEOUT)
    # The empty search is the compact index -- names only, and what a mistyped argument
    # name silently produces. Annotating it would spend bytes on an answer nobody asked
    # for, so notes arrive with the signatures, when a term brought them.
    return found if not search.split() else notes.annotate(found)


# ------------------------------------------------------------ reading back what MD hides
#
# Everything below runs inside MD as source text, through the same ``run`` op as
# md_call, so it works with any listener that answers ``run`` -- nothing new has to be
# installed in MD for it. What the fake host cannot prove is that MD does what these
# snippets expect of it; the measurements they rest on are named where they are used.

#: Where a snippet's parameters go: replaced by a Python literal of their JSON.
_PARAMS = "__MD_MCP_PARAMS__"

#: Shared by the snippets that read geometry. Runs inside MD.
#:
#: There is no 3D readback in the call API -- ``utility_api.GetClothPositions`` takes a
#: list and returns None, so nothing reaches Python -- and nothing reports an avatar's
#: size or a piece's 3D bounds. Exporting an OBJ and reading it is the route that was
#: measured to work (2026-10-11, 2026.0.315): millimetres, Y up, +Z the avatar's front.
#: ``ExportOBJ`` is only ever given its option object: the bare-path overload never
#: returned in 300s. And its return value is not believed: into a missing folder it
#: returned the path list of a successful export and wrote nothing.
_OBJ_HELPERS = r'''
import json, os, shutil, tempfile, time

_MDMCP_AXES = ("Y up; +Z is the avatar's front; millimetres. Measured on 2026.0.315 "
               "with ImportExportOption's default scale.")


def _mdmcp_paths(value, found=None):
    found = [] if found is None else found
    if isinstance(value, str):
        if value and len(value) < 1024 and "\n" not in value:
            found.append(value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _mdmcp_paths(item, found)
    return found


def _mdmcp_export_obj(folder, stem, avatar, garment):
    """Export one OBJ into folder; the path MD really wrote, or an exception."""
    import ApiTypes, export_api
    option = ApiTypes.ImportExportOption()
    option.bExportAvatar = avatar
    option.bExportGarment = garment
    path = os.path.join(folder, stem + ".obj").replace("\\", "/")
    export = export_api.ExportOBJ
    if not path.isascii() and hasattr(export_api, "ExportOBJW"):
        export = export_api.ExportOBJW
    returned = export(path, option)
    named = [p for p in _mdmcp_paths(returned) if p.lower().endswith(".obj")]
    for candidate in [path] + named:
        if os.path.isfile(candidate):
            return candidate
    raise RuntimeError(
        "ExportOBJ returned {!r} and wrote no .obj at {}: MD reported an export it "
        "did not make".format(returned, path))


def _mdmcp_read_obj(path, default_group):
    """All vertex positions, and the vertices of each group statement in turn.

    A group's vertices are the ones its faces use, since an OBJ may list every vertex
    before any group; a file with no faces falls back to the vertices listed under
    each group. Vertices before any group statement belong to default_group. A name
    seen twice is two entries, numbered by occurrence, never merged.
    """
    positions = []
    blocks = []
    current = None
    faces_seen = False
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            head = line[:2]
            if head in ("v ", "v\t"):
                parts = line.split()
                try:
                    positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except (IndexError, ValueError):
                    positions.append(None)
                if current is None:
                    current = [default_group, set(), []]
                    blocks.append(current)
                current[2].append(len(positions) - 1)
            elif head in ("f ", "f\t"):
                faces_seen = True
                if current is None:
                    current = [default_group, set(), []]
                    blocks.append(current)
                count = len(positions)
                for token in line.split()[1:]:
                    try:
                        index = int(token.split("/", 1)[0])
                    except ValueError:
                        continue
                    current[1].add(index - 1 if index > 0 else count + index)
            elif head in ("g ", "o ", "g\t", "o\t") or line.strip() in ("g", "o"):
                current = [line[1:].strip() or default_group, set(), []]
                blocks.append(current)
    groups = []
    occurrences = {}
    for name, faces, listed in blocks:
        members = sorted(faces) if faces_seen else listed
        points = [positions[i] for i in members
                  if 0 <= i < len(positions) and positions[i] is not None]
        if not points:
            continue
        occurrences[name] = occurrences.get(name, 0) + 1
        groups.append({"name": name, "occurrence": occurrences[name], "points": points})
    return [p for p in positions if p is not None], groups


def _mdmcp_box(points):
    low = [min(p[axis] for p in points) for axis in range(3)]
    high = [max(p[axis] for p in points) for axis in range(3)]
    return {
        "min": [round(v, 1) for v in low],
        "max": [round(v, 1) for v in high],
        "size": [round(b - a, 1) for a, b in zip(low, high)],
    }


def _mdmcp_centroid(points):
    count = float(len(points))
    return [round(sum(p[axis] for p in points) / count, 1) for axis in range(3)]


def _mdmcp_cleanup(folder, result):
    shutil.rmtree(folder, ignore_errors=True)
    if os.path.exists(folder):
        result["cleanup_incomplete"] = folder
'''


#: md_measure, inside MD. Two exports -- avatar alone, garment alone -- into a folder it
#: makes and removes. Nothing in the scene is touched.
_MEASURE = _OBJ_HELPERS + r'''
import export_api, pattern_api
_params = json.loads(__MD_MCP_PARAMS__)
_started = time.monotonic()
_result = {"units": "mm", "axes": _MDMCP_AXES}
_folder = tempfile.mkdtemp(prefix="md-mcp-measure-")
try:
    try:
        _avatars = list(export_api.GetAvatarNameList())
    except Exception as exc:
        _avatars = None
        _result["avatar_names_error"] = "{}: {}".format(type(exc).__name__, exc)
    if _avatars == []:
        _result["avatar"] = None
        _result["avatar_note"] = "no avatar in the scene: GetAvatarNameList() is empty"
    else:
        _positions, _groups = _mdmcp_read_obj(
            _mdmcp_export_obj(_folder, "avatar", True, False), "avatar")
        if not _positions:
            _result["avatar"] = None
            _result["avatar_note"] = "MD exported the avatar with no vertices in it"
        else:
            _avatar = {"names": _avatars, "vertices": len(_positions)}
            _avatar.update(_mdmcp_box(_positions))
            _avatar["groups"] = sorted(set(g["name"] for g in _groups))[:20]
            _result["avatar"] = _avatar
            if _params["heights"]:
                _half = _params["band_mm"] / 2.0
                _sections = []
                for _height in _params["heights"]:
                    _band = [p for p in _positions
                             if _height - _half <= p[1] <= _height + _half]
                    _section = {"height": _height, "vertices": len(_band)}
                    if _band:
                        _section["x"] = [round(min(p[0] for p in _band), 1),
                                         round(max(p[0] for p in _band), 1)]
                        _section["z"] = [round(min(p[2] for p in _band), 1),
                                         round(max(p[2] for p in _band), 1)]
                    _sections.append(_section)
                _result["cross_sections"] = {
                    "band_mm": _params["band_mm"], "sections": _sections}
    if _params["heights"] and "cross_sections" not in _result:
        _result["cross_sections"] = None
    if pattern_api.GetPatternCount() == 0:
        _result["garment"] = None
        _result["garment_note"] = "no pattern pieces in the scene"
    else:
        _positions, _groups = _mdmcp_read_obj(
            _mdmcp_export_obj(_folder, "garment", False, True), "garment")
        if not _positions:
            _result["garment"] = None
            _result["garment_note"] = "MD exported the garment with no vertices in it"
        else:
            _garment = {"vertices": len(_positions)}
            _garment.update(_mdmcp_box(_positions))
            _terms = _params["group_search"].lower().split()
            _listed = [g for g in _groups
                       if all(t in g["name"].lower() for t in _terms)]
            _entries = []
            for _group in _listed[:_params["max_groups"]]:
                _box = _mdmcp_box(_group["points"])
                _entry = {"name": _group["name"]}
                if _group["occurrence"] > 1:
                    _entry["occurrence"] = _group["occurrence"]
                _entry["vertices"] = len(_group["points"])
                _entry["min"] = _box["min"]
                _entry["max"] = _box["max"]
                _entry["centroid"] = _mdmcp_centroid(_group["points"])
                _entries.append(_entry)
            _garment["groups_total"] = len(_groups)
            _repeated = sorted(set(g["name"] for g in _groups if g["occurrence"] > 1))
            if _repeated:
                _garment["repeated_group_names"] = _repeated
            if _terms:
                _garment["groups_matching"] = len(_listed)
            if len(_listed) > len(_entries):
                _garment["groups_omitted"] = len(_listed) - len(_entries)
            _garment["groups"] = _entries
            _result["garment"] = _garment
finally:
    _mdmcp_cleanup(_folder, _result)
_result["elapsed_seconds"] = round(time.monotonic() - _started, 3)
print(json.dumps(_result))
'''


#: md_arrangement_points, inside MD. The method measured on 2026-10-11 (2026.0.315):
#: GetArrangementList() gives names and percentage offsets and no positions, and on a
#: tailed avatar the body points sat about a metre behind the back -- so the only way to
#: know where a point is, is to put something there and look. A probe piece is added
#: last, arranged at each point in turn, exported garment-only and measured, and
#: deleted. DeletePatternPiece renumbers every later piece, so the probe is found by its
#: own name before anything is deleted, and nothing is deleted unless the count says
#: exactly one piece was added.
_ARRANGEMENT = _OBJ_HELPERS + r'''
import pattern_api, export_api, uuid
_params = json.loads(__MD_MCP_PARAMS__)
_started = time.monotonic()
_PROBE_OUTLINE = [(0, 0, 0), (200, 0, 0), (200, 100, 0), (100, 140, 0), (0, 100, 0)]


def _mdmcp_point_name(entry):
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("ArrangementName", "Name", "name"):
            if isinstance(entry.get(key), str):
                return entry[key]
        for key, value in entry.items():
            if "name" in str(key).lower() and isinstance(value, str):
                return value
    if isinstance(entry, (list, tuple)):
        for value in entry:
            if isinstance(value, str):
                return value
    return str(entry)


def _mdmcp_state():
    state = {}
    reads = (
        ("pattern_count", pattern_api.GetPatternCount),
        ("seam_group_count", pattern_api.GetSeamlinePairGroupCount),
        ("fabrics", lambda: list(__import__("fabric_api").GetFabricStyleNameList())),
    )
    for key, read in reads:
        try:
            state[key] = read()
        except Exception as exc:
            state.setdefault("errors", {})[key] = "{}: {}".format(type(exc).__name__, exc)
    return state


def _mdmcp_probe_points(groups, probe_name, baseline):
    lowered = probe_name.lower()
    named = [g for g in groups if g["name"] == probe_name] or [
        g for g in groups if lowered in g["name"].lower()]
    if named:
        return [p for g in named for p in g["points"]], "its group name"
    if baseline is None:
        if groups:
            return ([p for g in groups for p in g["points"]],
                    "being the only piece in the scene")
    else:
        added = [g for g in groups if (g["name"], g["occurrence"]) not in baseline]
        if added:
            return ([p for g in added for p in g["points"]],
                    "the group the export gained when it was added")
    raise RuntimeError(
        "could not tell the probe from the rest of the garment in the export; its "
        "groups were {}".format(sorted(set(g["name"] for g in groups))[:20]))


def _mdmcp_remove_probe(probe, probe_name, named, before_count):
    count = pattern_api.GetPatternCount()
    if count != before_count + 1:
        return {"error": (
            "the pattern count is {} where the probe should have made it {}, so nothing "
            "was deleted: inspect the scene".format(count, before_count + 1))}
    index = None
    if named:
        first = [probe] if isinstance(probe, int) and 0 <= probe < count else []
        for candidate in first + list(range(count)):
            try:
                if pattern_api.GetPatternPieceName(candidate) == probe_name:
                    index = candidate
                    break
            except Exception:
                continue
    elif probe == count - 1:
        # Unnamed, so only the index vouches for it -- and only the last index can be
        # the piece that was just added.
        index = probe
    if index is None:
        return {"error": (
            "the pattern count rose by one but the probe could not be identified, so "
            "nothing was deleted: the last piece (index {}) is probably it".format(
                count - 1))}
    pattern_api.DeletePatternPiece(index)
    return {"deleted_index": index}


_names = [_mdmcp_point_name(entry) for entry in list(pattern_api.GetArrangementList())]
_result = {"units": "mm", "axes": _MDMCP_AXES, "available": len(_names)}
_chosen = []
_unmatched = []
if _params["points"] is None:
    _chosen = list(range(len(_names)))
else:
    for _want in _params["points"]:
        if isinstance(_want, int) and not isinstance(_want, bool):
            _hits = [_want] if 0 <= _want < len(_names) else []
        else:
            _text = str(_want)
            _hits = [i for i, n in enumerate(_names) if n == _text] or [
                i for i, n in enumerate(_names) if _text.lower() in n.lower()]
        if not _hits:
            _unmatched.append(_want)
        for _hit in _hits:
            if _hit not in _chosen:
                _chosen.append(_hit)
if _unmatched:
    _result["unmatched"] = _unmatched
_points = []
_failed = []
if not _chosen:
    _result["points"] = []
    _result["probe"] = {"used": False, "why": (
        "no arrangement point to resolve"
        + ("" if _names else ": GetArrangementList() is empty, which the shipped "
           "Genesis8 avatar was measured to be"))}
else:
    _before = _mdmcp_state()
    _folder = tempfile.mkdtemp(prefix="md-mcp-arrange-")
    _probe = None
    _attempted = False
    _probe_name = "mdmcp_probe_" + uuid.uuid4().hex[:8]
    _named = False
    _found_by = None
    _removal = {}
    try:
        if "pattern_count" not in _before:
            raise RuntimeError(
                "the pattern count cannot be read, so a probe could not be removed "
                "safely; no probe was added")
        _baseline = None
        if _before["pattern_count"]:
            _baseline_groups = _mdmcp_read_obj(
                _mdmcp_export_obj(_folder, "baseline", False, True), "garment")[1]
            _baseline = set((g["name"], g["occurrence"]) for g in _baseline_groups)
        _attempted = True
        _probe = pattern_api.CreatePatternWithPoints(_PROBE_OUTLINE)
        _count = pattern_api.GetPatternCount()
        if _count != _before["pattern_count"] + 1:
            raise RuntimeError(
                "CreatePatternWithPoints answered {!r} but the pattern count went from "
                "{} to {}".format(_probe, _before["pattern_count"], _count))
        if _probe != _before["pattern_count"]:
            # Naming or arranging any other index would edit somebody's piece.
            raise RuntimeError(
                "CreatePatternWithPoints answered index {!r}, not {}, the last one; "
                "nothing was done to that index".format(_probe, _before["pattern_count"]))
        try:
            pattern_api.SetPatternPieceName(_probe, _probe_name)
            _named = pattern_api.GetPatternPieceName(_probe) == _probe_name
        except Exception:
            _named = False
        for _index in _chosen:
            _entry = {"index": _index, "name": _names[_index]}
            try:
                if pattern_api.SetArrangement(_probe, _index) is False:
                    raise RuntimeError("SetArrangement returned False")
                _groups = _mdmcp_read_obj(
                    _mdmcp_export_obj(_folder, "probe_{}".format(_index), False, True),
                    "garment")[1]
                _mine, _found_by = _mdmcp_probe_points(_groups, _probe_name, _baseline)
                _box = _mdmcp_box(_mine)
                _entry["center"] = [round((a + b) / 2.0, 1)
                                    for a, b in zip(_box["min"], _box["max"])]
                _entry["min"] = _box["min"]
                _entry["max"] = _box["max"]
                _points.append(_entry)
            except Exception as exc:
                _entry["error"] = "{}: {}".format(type(exc).__name__, exc)
                _failed.append(_entry)
    except Exception as exc:
        _result["error"] = "{}: {}".format(type(exc).__name__, exc)
    finally:
        if _attempted:
            try:
                _removal = _mdmcp_remove_probe(
                    _probe, _probe_name, _named, _before["pattern_count"])
            except Exception as exc:
                _removal = {"error": "deleting the probe failed: {}: {}".format(
                    type(exc).__name__, exc)}
        _after = _mdmcp_state()
        _mdmcp_cleanup(_folder, _result)
    _result["points"] = _points
    if _failed:
        _result["failed"] = _failed
    _probe_report = {
        "used": _attempted,
        "what": (
            "Added one temporary pattern piece (a 200 x 140 mm probe named {}), "
            "arranged it at each point, exported the garment to see where it landed, "
            "and deleted it again.".format(_probe_name)),
        "pattern_count_before": _before.get("pattern_count"),
        "pattern_count_after": _after.get("pattern_count"),
        "seam_group_count_before": _before.get("seam_group_count"),
        "seam_group_count_after": _after.get("seam_group_count"),
        "fabrics_unchanged": _before.get("fabrics") == _after.get("fabrics"),
    }
    _probe_report["unchanged"] = (
        "pattern_count" in _after
        and _before.get("pattern_count") == _after.get("pattern_count")
        and _before.get("seam_group_count") == _after.get("seam_group_count")
        and _probe_report["fabrics_unchanged"])
    _probe_report.update(_removal)
    if _found_by:
        _probe_report["found_in_export_by"] = _found_by
    if _after.get("errors"):
        _probe_report["readback_errors"] = _after["errors"]
    _result["probe"] = _probe_report
_result["elapsed_seconds"] = round(time.monotonic() - _started, 3)
print(json.dumps(_result))
'''


#: md_snapshot, inside MD. SetCamViewPoint(0..9) is the camera call that works --
#: SetViewPoint is accepted and moves nothing -- and Refresh3DWindow goes between the
#: move and ExportSnapshot3D (measured 2026-09-19 and 2026-10-11, 2026.0.315). Several
#: views are taken in one run, so front, side and back cost one round trip. Each PNG
#: crosses the bridge as base64 inside the printed JSON, and the files are deleted.
_SNAPSHOT = r'''
import base64, json, os, shutil, tempfile
import export_api, utility_api
_params = json.loads(__MD_MCP_PARAMS__)


def _mdmcp_paths(value, found):
    if isinstance(value, str):
        if value and len(value) < 1024 and "\n" not in value:
            found.append(value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _mdmcp_paths(item, found)
    return found


_shots = []
_folder = tempfile.mkdtemp(prefix="md-mcp-snapshot-")
try:
    for _number, _view in enumerate(_params["views"]):
        _shot = {"view": _view, "camera_moved": False}
        if _view is not None:
            utility_api.SetCamViewPoint(_view)
            _shot["camera_moved"] = True
        if hasattr(utility_api, "Refresh3DWindow"):
            utility_api.Refresh3DWindow()
        _path = os.path.join(_folder, "snapshot_{}.png".format(_number)).replace("\\", "/")
        _returned = export_api.ExportSnapshot3D(_path)
        _found = None
        for _candidate in [_path] + _mdmcp_paths(_returned, []):
            if os.path.isfile(_candidate):
                _found = _candidate
                break
        if _found is None:
            raise RuntimeError(
                "ExportSnapshot3D returned {!r} and wrote nothing at {}".format(
                    _returned, _path))
        with open(_found, "rb") as _handle:
            _data = _handle.read()
        if not _data:
            raise RuntimeError("ExportSnapshot3D wrote an empty file at {}".format(_found))
        _shot["image_base64"] = base64.b64encode(_data).decode("ascii")
        _shots.append(_shot)
finally:
    shutil.rmtree(_folder, ignore_errors=True)
print(json.dumps({"shots": _shots}))
'''


#: md_fabric_presets, inside MD. AddFabric wants a .zfab path, and the shipped presets
#: were found only by searching the disk (2026-10-11): Public Documents'
#: MarvelousDesigner tree ("New Assets\Fabric") and the "Preset" folder of MD's install,
#: which is the folder of sys.executable inside MD. The install is located from the
#: running MD rather than from any path written down here; the user's own Documents are
#: searched too. Folders are asked of Windows first and the environment second.
_FABRIC_PRESETS = r'''
import json, os, sys
_params = json.loads(__MD_MCP_PARAMS__)


def _mdmcp_shell_folder(csidl):
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0:
            return buffer.value or None
    except Exception:
        return None
    return None


_roots = []
_install = os.path.dirname(sys.executable) if sys.executable else ""
for _base in (_install, os.path.dirname(_install) if _install else "",
              getattr(sys, "prefix", "") or ""):
    if _base:
        _roots.append(("md install", os.path.join(_base, "Preset")))
_public = _mdmcp_shell_folder(0x2E)
if _public:
    _roots.append(("public documents", os.path.join(_public, "MarvelousDesigner")))
if os.environ.get("PUBLIC"):
    _roots.append(("public documents",
                   os.path.join(os.environ["PUBLIC"], "Documents", "MarvelousDesigner")))
_documents = _mdmcp_shell_folder(0x05)
if _documents:
    _roots.append(("user documents", os.path.join(_documents, "MarvelousDesigner")))
_roots.append(("user documents",
               os.path.join(os.path.expanduser("~"), "Documents", "MarvelousDesigner")))

_terms = _params["search"].lower().split()
_searched = []
_seen_roots = set()
_seen_files = set()
_folders = {}
_total = 0
for _source, _root in _roots:
    _root = os.path.normpath(_root)
    _key = os.path.normcase(_root)
    if _key in _seen_roots:
        continue
    _seen_roots.add(_key)
    if not os.path.isdir(_root):
        _searched.append({"root": _root, "source": _source, "exists": False})
        continue
    _here = 0
    for _dir, _subdirs, _files in os.walk(_root):
        _subdirs.sort()
        for _file in sorted(_files):
            if not _file.lower().endswith(".zfab"):
                continue
            _full = os.path.join(_dir, _file)
            if os.path.normcase(_full) in _seen_files:
                continue
            _seen_files.add(os.path.normcase(_full))
            _here += 1
            if _terms and not all(t in _full.lower() for t in _terms):
                continue
            _folders.setdefault(_dir, {"folder": _dir, "source": _source, "fabrics": []})
            _folders[_dir]["fabrics"].append(
                {"name": os.path.splitext(_file)[0], "path": _full})
    _total += _here
    _searched.append({"root": _root, "source": _source, "exists": True,
                      "zfab_files": _here})
print(json.dumps({
    "md_executable": sys.executable,
    "count": sum(len(f["fabrics"]) for f in _folders.values()),
    "total": _total,
    "roots": _searched,
    "folders": list(_folders.values()),
}))
'''


#: md_seams, inside MD. ExportPatternJSON is the only place MD says which edges a seam
#: joins; the document goes to a temporary folder and only what the ledger needs comes
#: back -- each piece's id, name, index and line lengths (MD's own GetLineLength, and the
#: document's geometry as a cross-check on line order), and the seam groups as MD wrote
#: them. Decoding happens in md_mcp.seams, where it can be tested without MD.
_SEAMS = r'''
import json, math, os, shutil, tempfile
import pattern_api
_folder = tempfile.mkdtemp(prefix="md-mcp-seams-")
try:
    _path = os.path.join(_folder, "pattern.json")
    pattern_api.ExportPatternJSON(_path)
    if not os.path.isfile(_path):
        raise RuntimeError("ExportPatternJSON wrote no file at {}".format(_path))
    with open(_path, encoding="utf-8") as _handle:
        _document = json.load(_handle)
    _count = pattern_api.GetPatternCount()
    _names = [pattern_api.GetPatternPieceName(_i) for _i in range(_count)]
    _pieces = []
    for _order, _piece in enumerate(_document.get("PatternList", [])):
        _lines = (_piece.get("ShapeInfo") or {}).get("LineList") or []
        _polyline = []
        for _line in _lines:
            _points = [(_p["Position"]["x"], _p["Position"]["y"])
                       for _p in _line.get("PointList", [])]
            _polyline.append(sum(math.hypot(_b[0] - _a[0], _b[1] - _a[1])
                                 for _a, _b in zip(_points, _points[1:])))
        _name = _piece.get("Name")
        # The document lists pieces in MD's order; the name confirms it, and a name
        # that occurs once settles it if the order ever differs.
        if _order < _count and _names[_order] == _name:
            _index = _order
        elif _names.count(_name) == 1:
            _index = _names.index(_name)
        else:
            _index = None
        _measured = None
        if _index is not None:
            try:
                _measured = [float(pattern_api.GetLineLength(_index, _j))
                             for _j in range(len(_lines))]
            except Exception:
                _measured = None
        _pieces.append({"id": _piece.get("ID"), "name": _name, "index": _index,
                        "line_lengths": _measured, "polyline_lengths": _polyline})
    _result = {"pieces": _pieces,
               "groups": _document.get("SeamLinePairGroupList", []),
               "seam_group_count": pattern_api.GetSeamlinePairGroupCount()}
except Exception as _exc:
    _result = {"error": "{}: {}".format(type(_exc).__name__, _exc)}
finally:
    shutil.rmtree(_folder, ignore_errors=True)
print(json.dumps(_result))
'''


#: What both checkpoint snippets share: MD's open project and its unsaved-changes flag,
#: and the one way found to set that flag again. Measured on 2026.0.315 (2026-10-11):
#: every save of a copy -- ExportZPrj in all three forms, ExportZPac, and ImportZpac
#: too -- clears CheckZPRJForUnsavedChanges, so MD would close without asking although
#: the user's own file lacks their latest work. Renaming a piece does not set it again,
#: even to another name; creating a piece does, and deleting it afterwards leaves the
#: flag set and the garment as it was. The temporary piece is created last, so deleting
#: it renumbers nothing, and it is found by its own name before anything is deleted.
_PROJECT_STATE = r'''
import utility_api, pattern_api


def _mdmcp_project():
    _state = {}
    for _key, _name in (("path", "GetProjectFilePath"), ("name", "GetProjectName"),
                        ("unsaved", "CheckZPRJForUnsavedChanges")):
        _call = getattr(utility_api, _name, None)
        if _call is None:
            _state[_key + "_error"] = "this build has no " + _name
            continue
        try:
            _state[_key] = _call()
        except Exception as _exc:
            _state[_key + "_error"] = "{}: {}".format(type(_exc).__name__, _exc)
    return _state


def _mdmcp_mark_unsaved():
    _count = pattern_api.GetPatternCount()
    _temporary = pattern_api.CreatePatternWithPoints(
        [(-9000, -9000, 0), (-8990, -9000, 0), (-8990, -8990, 0), (-9000, -8990, 0)])
    if pattern_api.GetPatternCount() != _count + 1 or _temporary != _count:
        raise RuntimeError(
            "the temporary piece that marks the scene unsaved came back as index {!r} "
            "with {} pieces; nothing was deleted -- inspect the scene".format(
                _temporary, pattern_api.GetPatternCount()))
    _name = "mdmcp_unsaved_marker"
    pattern_api.SetPatternPieceName(_temporary, _name)
    if pattern_api.GetPatternPieceName(_count) != _name:
        raise RuntimeError("could not identify the temporary piece; nothing was deleted")
    pattern_api.DeletePatternPiece(_count)
    return bool(utility_api.CheckZPRJForUnsavedChanges())
'''

#: md_checkpoint, inside MD. ExportZPac, not ExportZPrj: measured on 2026.0.315
#: (2026-10-11), ExportZPrj in all three forms re-points MD's open project at the copy,
#: so the user's next Save would write over the checkpoint instead of their work.
#: ExportZPac left the project path alone -- an unsaved project stayed NULL and a saved
#: one kept its path -- and carries the garment, not the avatar.
_CHECKPOINT = _PROJECT_STATE + r'''
import json, os
import export_api
_params = json.loads(__MD_MCP_PARAMS__)
_result = {"project_before": _mdmcp_project()}
_path = _params["path"]
_parent = os.path.dirname(_path)
if _parent and not os.path.isdir(_parent):
    os.makedirs(_parent)
_returned = export_api.ExportZPac(_path)
_result["returned"] = _returned if isinstance(_returned, (bool, int, str)) else repr(_returned)
_result["bytes"] = os.path.getsize(_path) if os.path.isfile(_path) else 0
_after = _mdmcp_project()
if _result["project_before"].get("unsaved") and not _after.get("unsaved"):
    _result["unsaved_restored"] = _mdmcp_mark_unsaved()
    _after = _mdmcp_project()
_result["project_after"] = _after
print(json.dumps(_result))
'''

#: md_restore, inside MD. A .zpac goes back with ImportZpac, which replaced the garment
#: and kept the avatar (measured 2026-10-11, onto a scene with an extra piece: the extra
#: piece went, the avatar stayed). Any .zprj goes back with ImportZprj, which replaces
#: everything and re-points MD's project at that file. Both need their option object.
#: Either way the scene now differs from the user's saved file, so the unsaved flag the
#: import cleared is set again.
_RESTORE = _PROJECT_STATE + r'''
import json, os
import ApiTypes, import_api
_params = json.loads(__MD_MCP_PARAMS__)
_path = _params["path"]
if not os.path.isfile(_path):
    raise FileNotFoundError("no checkpoint at {}".format(_path))
if _path.lower().endswith(".zprj"):
    _returned = import_api.ImportZprj(_path, ApiTypes.ImportZPRJOption())
else:
    _returned = import_api.ImportZpac(_path, ApiTypes.ImportExportOption())
_result = {"returned": _returned if isinstance(_returned, (bool, int, str))
           else repr(_returned)}
_result["unsaved_restored"] = _mdmcp_mark_unsaved()
_result["project_after"] = _mdmcp_project()
print(json.dumps(_result))
'''


def _decode_printed(output: str) -> Any:
    """The JSON document a snippet printed -- its last line if something printed first."""
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass
    for line in reversed(output.splitlines()):
        if line.strip():
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                break
    raise BridgeError(f"Expected one JSON document from MD, got: {output[:400]!r}")


def _run_snippet(
    source: str, params: dict, name: str, timeout: float = RUN_TIMEOUT
) -> tuple[dict, dict]:
    """Run one of the snippets above with ``params``: what it printed, and the reply."""
    script = source.replace(_PARAMS, repr(json.dumps(params)))
    reply = request("run", timeout=timeout, source=script, name=name)
    return _decode_printed(reply.get("output", "")), reply


def measure(
    heights: list[float] | None = None,
    band_mm: float = 20.0,
    group_search: str = "",
    max_groups: int = 100,
) -> dict:
    """The avatar's and the garment's 3D extent, read from OBJ exports inside MD."""
    measured, _ = _run_snippet(
        _MEASURE,
        {
            "heights": [float(h) for h in heights or []],
            "band_mm": float(band_mm),
            "group_search": group_search,
            "max_groups": int(max_groups),
        },
        "<md-mcp measure>",
    )
    return measured


def arrangement_points(
    points: list | None = None, timeout: float | None = None
) -> dict:
    """Where each arrangement point really is, found by arranging a probe piece there.

    Raises :class:`BridgeError` when the run as a whole failed, with the probe check in
    the message: the part a caller must not lose is whether the scene was put back.
    """
    resolved, reply = _run_snippet(
        _ARRANGEMENT,
        {"points": points},
        "<md-mcp arrangement points>",
        timeout=RUN_TIMEOUT if timeout is None else timeout,
    )
    probe = resolved.get("probe") or {}
    # The listener reads the scene back after every run, independently of the script,
    # so the count it reports is a second witness to the probe having gone.
    if "pattern_count" in reply and probe.get("used"):
        probe["pattern_count_read_back"] = reply["pattern_count"]
        if reply["pattern_count"] != probe.get("pattern_count_before"):
            probe["unchanged"] = False
    if resolved.get("error"):
        raise BridgeError(
            f"Resolving arrangement points failed: {resolved['error']}\n"
            f"Probe check: {json.dumps(probe)}"
        )
    return resolved


def snapshots(views: list[int | None]) -> list[tuple[bytes, dict]]:
    """PNGs of MD's 3D window, one per view, taken in one run. None is "as it is"."""
    taken, _ = _run_snippet(_SNAPSHOT, {"views": list(views)}, "<md-mcp snapshot>")
    result = []
    for shot in taken.get("shots", []):
        encoded = shot.pop("image_base64", None)
        if not encoded:
            raise BridgeError(f"MD's snapshot came back without an image: {shot!r}")
        result.append((base64.b64decode(encoded), shot))
    if len(result) != len(views):
        raise BridgeError(
            f"asked MD for {len(views)} snapshot(s) and got {len(result)}: {taken!r}"
        )
    return result


#: The scene fields every run reply carries, read back by the listener after the run.
SCENE_KEYS = ("pattern_count", "pieces", "seam_group_count", "avatars", "fabrics")


def checkpoint(path: str) -> tuple[dict, dict]:
    """Save the scene to ``path``: what the save reported, and the scene read back."""
    saved, reply = _run_snippet(_CHECKPOINT, {"path": path}, "<md-mcp checkpoint>")
    return saved, {key: reply[key] for key in SCENE_KEYS if key in reply}


def restore(path: str) -> tuple[dict, dict]:
    """Replace the scene with the checkpoint at ``path``: the import's answer, and the
    scene read back afterwards."""
    loaded, reply = _run_snippet(_RESTORE, {"path": path}, "<md-mcp restore>")
    return loaded, {key: reply[key] for key in SCENE_KEYS if key in reply}


def seams() -> dict:
    """The pieces, line lengths and seam groups ``md_mcp.seams.ledger`` decodes."""
    raw, _ = _run_snippet(_SEAMS, {}, "<md-mcp seams>")
    if raw.get("error"):
        raise BridgeError(f"Reading the seams out of MD failed: {raw['error']}")
    return raw


def fabric_presets(search: str = "") -> dict:
    """The .zfab files MD ships and the user keeps, found from inside MD."""
    found, _ = _run_snippet(
        _FABRIC_PRESETS, {"search": search}, "<md-mcp fabric presets>", PRESET_TIMEOUT
    )
    return found
