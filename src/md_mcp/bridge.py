"""The live bridge: md-mcp's side of the socket Marvelous Designer listens on.

MD cannot be dialled into by anything it does not host itself, so the connection is a
listener running inside MD (``plugin/md_mcp_listener.py``) that md-mcp connects to. One
JSON object per line each way, then the connection closes; see the listener's docstring
for the protocol.

A failure here is nearly always "MD is running but the listener was never started", so
every error carries the instruction to start it.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
from typing import Any

from md_mcp import notes

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
        f"No Marvelous Designer listener on {HOST}:{port()}. In MD, click 'md-mcp' in "
        "the Plugin tab and press Start in the window it opens -- the window comes up "
        "idle, so opening it is not enough. If there is no such entry yet, run "
        f"{where} via Main Menu > Plugins > Python Editor > Run, which opens the same "
        "window and adds the entry. It then serves for the rest of the MD session. "
        "MD's UI stays usable, but it is the listener that holds MD's main thread, so "
        "leave the editor's Run in progress -- it is not stuck."
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
    goes with it, because the listener *is* a script running on MD's main thread.

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
        "listener was stopped instead: press Start in the md-mcp window."
    )


def request(op: str, timeout: float = QUERY_TIMEOUT, **payload: Any) -> dict:
    """Send one request, return the reply. Raises :class:`BridgeError` on any failure."""
    message = json.dumps({"op": op, **payload}) + "\n"
    try:
        client = socket.create_connection((HOST, port()), timeout=timeout)
    except OSError as exc:
        raise BridgeError(how_to_connect()) from exc

    chunks: list[bytes] = []
    with client:
        client.settimeout(timeout)
        try:
            client.sendall(message.encode("utf-8"))
            while not chunks or b"\n" not in chunks[-1]:
                chunk = client.recv(65536)
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
        raise BridgeError(
            f"Marvelous Designer refused {op!r}: {detail}"
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
                "old one -- Stop and Start it in the md-mcp window.)"
                if stamp
                else ""
            )
        )
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


def _printed_json(source: str, timeout: float) -> Any:
    """Run a snippet that prints one JSON document, and return what it printed."""
    reply = request("run", timeout=timeout, source=source, name="<md-mcp query>")
    output = reply.get("output", "")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise BridgeError(
            f"Expected one JSON document from MD, got: {output[:400]!r}"
        ) from exc


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
    module, name = _split(function)
    source = (
        f"import json, {module}\n"
        f"_args = json.loads({json.dumps(json.dumps(args or []))})\n"
        f"print(json.dumps({{'result': {module}.{name}(*_args)}}, default=repr))\n"
    )
    try:
        return _printed_json(source, RUN_TIMEOUT)["result"]
    except BridgeError as exc:
        raise _explained(function, exc) from exc


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
