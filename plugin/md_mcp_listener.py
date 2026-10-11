"""md-mcp listener — the live bridge, run inside Marvelous Designer.

Two entries in MD's Plugin tab: **Start MCP** runs this file, and **Stop MCP** runs
``md_mcp_stop.py`` beside it. There is no window.

Start opens a loopback port, arms a Windows timer that serves it, and **returns**. From
then on Windows calls the timer from MD's own message loop, on MD's main thread, every
few milliseconds for as long as MD runs; each tick accepts what has arrived, answers any
request that is complete, and gives the thread straight back. Stop kills the timer and
closes the port. Closing MD ends the process as it should, because nothing is holding
MD's main thread in between.

Click **Start MCP** in the Plugin tab if it has been registered there, or run the
two-line bootstrap from the README in **Main Menu > Plugins > Python Editor**. The first
top-level run also registers both entries, so every session after it is a click (see
`register_plugin`).

Starting is a click and never a side effect: `run` executes arbitrary Python inside MD,
so the port opens because somebody asked for it.

Why a timer, and not a thread or a loop
---------------------------------------
MD embeds CPython and only lets its threads run while a script is executing. The moment
a script returns, the host stops releasing the GIL and **every Python thread freezes** —
measured on 2026.0.315: a thread ticked 20 times while the main thread slept 2s, and not
once after the script ended. A listener on a background thread therefore binds its port
(so ``connect()`` succeeds) and then answers nothing: it looks connected and is dead.

Earlier versions of this file were a script that never returned, pumping MD's message
queue between requests so that MD stayed usable. That worked, and it held MD's main
thread for the whole session: closing MD left the process running behind it.

MD's API calls nothing back, but Windows does. A ``user32.SetTimer`` thread timer is
dispatched by MD's own message loop after the script that armed it has returned —
measured on 2026.0.315 (2026-10-11): 100 requests at a 31 ms median round trip, MD's API
answering inside the callback, MD responsive throughout, and the process gone the moment
MD was closed. Every MD call still happens on the main thread, the only one MD documents
as safe. While a request is executing MD is unresponsive for exactly as long as the work
takes, as it is when Simulate is clicked in the UI.

Protocol: one JSON object per line in, one JSON object per line out, then the connection
closes.

    {"op": "ping"}                       -> {"ok": true, "md_version": "...", ...}
    {"op": "scene"}                      -> {"ok": true, "pattern_count": n, "pieces": []}
    {"op": "run", "source": "...python"} -> {"ok": true, "output": "...", "writes": [],
                                             + scene}
    {"op": "shutdown"}                   -> {"ok": true}

``writes`` is always present in a ``run`` reply from this version on, empty when the script
exported nothing, so a client can tell "nothing was written" from "this listener does not
check" (see `_checking_writes`).

Loopback only, and no authentication: anything that can reach 127.0.0.1 on this port can
run Python inside MD. That is the same trust boundary as MD's own script menu.
"""

import builtins
import contextlib
import ctypes
import functools
import io
import json
import os
import socket
import threading
import time
import traceback

import pattern_api
import utility_api

#: Stamped by ``tools/build_plugin.py`` when this file is packaged for release. A
#: checkout says so rather than claiming a version nobody built. Both travel in every
#: ``ping``, with the path the running listener was loaded from: a stale copy left
#: behind by an earlier install is otherwise indistinguishable from the current one,
#: and answers just as confidently.
__version__ = "0.0.0+source"
BUILD = "source checkout"

HOST = "127.0.0.1"
#: Must match ``MD_MCP_PORT`` on the md-mcp side.
PORT = int(os.environ.get("MD_MCP_PORT", "5121"))

#: How long a connected peer has to finish sending its request line once it has started.
REQUEST_TIMEOUT = 5.0

#: How long a peer that has sent *nothing at all* is kept before it is dropped -- a port
#: scanner, a stray browser tab. Peers are read a tick at a time, so a silent one no
#: longer holds anybody up; this only stops them accumulating. A client sends
#: immediately after connecting (md_mcp.bridge does it in the next statement), so a
#: second is already generous.
SILENT_PEER_TIMEOUT = 1.0

#: How often the timer asks Windows to call it. Windows rounds a thread timer up to its
#: clock tick, normally 15.6 ms, and 10 ms is the shortest it accepts at all.
TICK_MS = 10

#: Listener state lives on ``builtins`` so that a later run -- Stop MCP, or Start MCP
#: clicked again -- finds it even though MD gives each run a fresh namespace. Keyed by
#: port, because the tests run several listeners at once. ``md_mcp_stop.py`` reads the
#: same name.
_REGISTRY = "__md_mcp_listeners__"

#: Every timer callback this process has handed to Windows, kept for good. A callback
#: freed while Windows can still call it -- or while it is running, which is where a
#: shutdown request kills its own timer -- is a call into freed memory, and that ends
#: MD. One small object per Start is a price worth paying not to find out.
_KEEP_ALIVE = "__md_mcp_timer_callbacks__"


def _registry():
    table = getattr(builtins, _REGISTRY, None)
    if table is None:
        table = {}
        setattr(builtins, _REGISTRY, table)
    return table


def _md_version():
    return "{}.{}.{}".format(
        utility_api.GetMajorVersion(),
        utility_api.GetMinorVersion(),
        utility_api.GetPatchVersion(),
    )


def _readback(scene, key, read):
    """Record one readback, or why it could not be made -- never raise.

    A scene readback runs after every build, so a call this build does not have must
    not turn a garment that was made into a request that failed. The failure is
    reported in place of the value instead of replacing it with something plausible:
    ``0`` here would read as "nothing was sewn", which is the answer that gets a
    correct scene thrown away.
    """
    try:
        scene[key] = read()
    except Exception as exc:
        scene.setdefault("readback_errors", {})[key] = "{}: {}".format(
            type(exc).__name__, exc
        )


def _scene():
    """What MD ended up with — read back, never echoed from the request.

    Deliberately more than the pattern pieces. Sewing calls reject missing indices,
    but accept valid indices even when the wrong edges are paired. Count the seam
    groups and inspect the resulting scene. Avatars and fabrics are here too because
    they are what the next call depends on.
    """
    scene = {}
    _readback(scene, "pattern_count", pattern_api.GetPatternCount)
    if "pattern_count" in scene:
        _readback(scene, "pieces", lambda: [
            {"index": index, "name": pattern_api.GetPatternPieceName(index)}
            for index in range(scene["pattern_count"])
        ])
    _readback(scene, "seam_group_count", lambda: pattern_api.GetSeamlinePairGroupCount())
    _readback(scene, "avatars", lambda: list(__import__("export_api").GetAvatarNameList()))
    # Not GetFabricCount(), which answers 0 for a scene that plainly has a fabric in it
    # (measured on 2026.0.315). The style list is the one that tells the truth.
    _readback(
        scene, "fabrics", lambda: list(__import__("fabric_api").GetFabricStyleNameList())
    )
    return scene


# ------------------------------------------------------------------------ the handler


#: What MD injects into its own Python Editor. A script pasted into that editor can
#: use them without importing, so scripts written against MD's documentation -- and the
#: habits of anyone who has used it -- assume they are already there. Seeding them here
#: costs nothing and removes a NameError that every first script otherwise hits.
SCRIPT_MODULES = (
    "pattern_api",
    "utility_api",
    "export_api",
    "import_api",
    "fabric_api",
    "ApiTypes",
)


def _script_namespace(name):
    """A fresh namespace with MD's modules already bound, where they can be bound.

    Imported one at a time and never fatally: this file is also executed by the test
    suite against fake host modules, and on a machine where some of these do not exist
    a missing module must leave the others usable rather than failing the run.
    """
    namespace = {"__name__": "__md_mcp__", "__file__": name or "<md-mcp>"}
    for module in SCRIPT_MODULES:
        try:
            namespace[module] = __import__(module)
        except ImportError:
            pass
    return namespace


# ------------------------------------------------------------ checking what was written


#: Where MD's file-writing calls live. Every ``Export*`` member of these is checked for
#: the length of one script -- see `_checking_writes`. ``ExportPatternJSON`` is on
#: pattern_api, not export_api.
WRITE_MODULES = ("export_api", "pattern_api")

#: ``Export*`` names that take no path and write nothing: this one returns a short
#: listing of the scene's fabrics and trims as a string (measured on 2026.0.315).
NOT_WRITES = frozenset({"ExportObjectBrowserMaterialsList"})

#: How many write records one reply carries. A script exporting in a loop -- one export
#: per arrangement point, say -- would otherwise put hundreds in every reply. Records
#: with a problem are kept ahead of clean ones.
MAX_WRITE_RECORDS = 20


def _looks_like_path(value):
    return (
        isinstance(value, str)
        and 0 < len(value) < 1024
        and "\n" not in value
        and ("/" in value or "\\" in value)
        and not value.lstrip().startswith(("{", "["))
    )


def _paths_in(value, found, depth=0):
    """Every path-shaped string in a return value, however MD nested it.

    ``ExportSnapshot3D`` answers ``[['C:/.../shot.png']]`` and ``ExportOBJ`` a flat list,
    so nesting is followed rather than assumed.
    """
    if depth > 4:
        return found
    if isinstance(value, str):
        if _looks_like_path(value):
            found.append(value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _paths_in(item, found, depth + 1)
    return found


def _distinct(paths):
    seen = set()
    kept = []
    for path in paths:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            kept.append(path)
    return kept


def _write_checked(qualified, original, records):
    """``original``, plus a record of whether the files it reported are really there.

    Measured on 2026.0.315 (2026-10-11): ``ExportOBJ`` into a folder that did not exist
    returned its path list exactly as a successful export does, and wrote nothing. So
    the return value of a writing call is not evidence, and the check is made where the
    files are -- inside MD, straight after the call, against MD's own idea of a relative
    path.

    A missing parent folder of an absolute file path is created before the call, and
    the record says so: that is the case MD reports as success and silently drops.
    Arguments and the return value pass through untouched, and an exception from the
    call is re-raised as it was.
    """

    @functools.wraps(original)
    def checked(*args, **kwargs):
        target = next((arg for arg in args if isinstance(arg, str)), None)
        names_a_file = bool(target) and bool(os.path.splitext(target)[1])
        record = {"call": qualified}
        if target is not None:
            record["target"] = target
        if names_a_file and os.path.isabs(target):
            parent = os.path.dirname(target)
            if parent and not os.path.isdir(parent):
                try:
                    os.makedirs(parent)
                    record["created_folders"] = [parent]
                except OSError as exc:
                    record["folder_error"] = "{}: {}".format(type(exc).__name__, exc)
        try:
            value = original(*args, **kwargs)
        except BaseException as exc:
            if "created_folders" in record or "folder_error" in record:
                record["raised"] = "{}: {}".format(type(exc).__name__, exc)
                records.append(record)
            raise
        expected = _distinct(_paths_in(value, []) + ([target] if names_a_file else []))
        if not expected:
            if _looks_like_path(target):
                record["unverified"] = (
                    "it named no file and returned no path, so there was nothing to check"
                )
                records.append(record)
            return value
        missing = [path for path in expected if not os.path.exists(path)]
        record["written"] = [path for path in expected if path not in missing]
        if missing:
            record["missing_outputs"] = missing
        empty = [
            path for path in record["written"]
            if os.path.isfile(path) and os.path.getsize(path) == 0
        ]
        if empty:
            record["empty_outputs"] = empty
        records.append(record)
        return value

    return checked


def _checking_writes(records):
    """Check every ``Export*`` call for the length of one script. Returns the undo list.

    The module attribute is replaced rather than the script's namespace, because a
    script that says ``import export_api`` rebinds the name to the module itself, and
    the module is what md_call's generated source calls too. Never raises: a call that
    cannot be wrapped is left as it is, unchecked.
    """
    installed = []
    for module_name in WRITE_MODULES:
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        for name in dir(module):
            if not name.startswith("Export") or name in NOT_WRITES:
                continue
            try:
                original = getattr(module, name)
                if not callable(original) or isinstance(original, type):
                    continue
                wrapper = _write_checked(module_name + "." + name, original, records)
                setattr(module, name, wrapper)
            except Exception:
                continue
            installed.append((module, name, original, wrapper))
    return installed


def _stop_checking_writes(installed):
    """Put back every call `_checking_writes` replaced -- unless the script replaced it."""
    for module, name, original, wrapper in reversed(installed):
        with contextlib.suppress(Exception):
            if getattr(module, name, None) is wrapper:
                setattr(module, name, original)


#: Keys that make a write record worth reading before the clean ones.
_WRITE_PROBLEMS = frozenset(
    {"missing_outputs", "empty_outputs", "created_folders", "folder_error", "raised",
     "unverified"}
)


def _write_report(records):
    problems = [record for record in records if _WRITE_PROBLEMS & set(record)]
    clean = [record for record in records if not _WRITE_PROBLEMS & set(record)]
    report = {"writes": (problems + clean)[:MAX_WRITE_RECORDS]}
    if len(records) > MAX_WRITE_RECORDS:
        report["writes_omitted"] = len(records) - MAX_WRITE_RECORDS
    return report


def _run(source, name):
    """Execute a script in a namespace of its own, capturing everything it printed.

    ``sys.stdout`` is redirected rather than ``print`` rebound, so that output from
    anything the script *calls* is captured too. That is safe here only because this
    runs inside the timer's tick on MD's main thread, and a tick never runs inside
    another: nothing else in the process can write to the stream while it is swapped
    out.

    MD's ``Export*`` calls are checked while it runs: see `_checking_writes`.
    """
    captured = io.StringIO()
    namespace = _script_namespace(name)
    started = time.monotonic()
    result = {"ok": True, "execution_succeeded": True}
    writes = []
    installed = _checking_writes(writes)
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            exec(compile(source, name or "<md-mcp>", "exec"), namespace)
    except (Exception, SystemExit) as exc:
        # Earlier statements are not rolled back. Preserve their output and let the
        # caller inspect the resulting scene even when a later statement fails.
        result.update(
            ok=False, execution_succeeded=False, error=str(exc),
            traceback=traceback.format_exc(),
            plugin_version=__version__, plugin_build=BUILD,
        )
    finally:
        _stop_checking_writes(installed)
    result["output"] = captured.getvalue()
    result["elapsed_seconds"] = time.monotonic() - started
    result.update(_write_report(writes))
    return result


def _handle(request, state):
    op = request.get("op")
    if op == "ping":
        return {
            "ok": True,
            "md_version": _md_version(),
            "pid": os.getpid(),
            "plugin_version": __version__,
            "plugin_build": BUILD,
            "plugin_path": _this_file(),
        }
    if op == "scene":
        return dict(ok=True, **_scene())
    if op == "run":
        source = request.get("source")
        if not source:
            return {"ok": False, "error": "run needs a 'source' of Python to execute"}
        result = _run(source, request.get("name"))
        # Always return a self-contained scene. Queries and other clients also use
        # run, so a listener-global cache cannot know what this caller has seen.
        result.update(_scene())
        return result
    if op == "shutdown":
        state["stop"] = True
        return {"ok": True, "stopped": True}
    return {"ok": False, "error": "unknown op {!r}".format(op)}


# ---------------------------------------------------------------- serving, a tick at a time


def _reply_to(connection, raw, state):
    """Answer one request line on ``connection``. Never raises."""
    # Deliberately inside the catch: an exception raised by the *script* -- an export to
    # a locked path is the everyday one -- is an answer the caller needs, not a dead
    # connection. Swallowed, it reads as "the listener stopped", and MD gets restarted
    # over a bridge that never broke.
    try:
        reply = _handle(json.loads(raw.decode("utf-8")), state)
    except (Exception, SystemExit) as exc:
        # Imported scripts may use sys.exit() for early termination. Report that
        # request's exit without shutting down the listener; leave user interrupts
        # free to stop the running script.
        # The build is on every failure on purpose. A traceback from here quotes
        # md_mcp_listener.py by path, and Python reads that file off disk when it
        # formats the frame -- so once the file has been edited under a listener
        # that is still running, the line numbers are this instance's and the source
        # lines beside them are the new file's. The result points at unrelated code
        # and reads as a bug in the listener. Naming the build that actually raised
        # it is what makes "you are looking at a stale listener" visible.
        reply = {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "plugin_version": __version__,
            "plugin_build": BUILD,
        }
    with contextlib.suppress(OSError):
        # Blocking for the send, with a limit: a peer that stops reading could
        # otherwise hold MD's main thread for as long as it liked.
        connection.settimeout(REQUEST_TIMEOUT)
        connection.sendall((json.dumps(reply) + "\n").encode("utf-8"))


def _accept_waiting(state):
    """Take every connection waiting on the port, without waiting for any."""
    while True:
        listener = state["listener"]
        if listener is None:
            return
        try:
            connection, _ = listener.accept()
        except (BlockingIOError, InterruptedError, socket.timeout):
            return
        except OSError:
            # Windows raises WSAECONNRESET from accept() when a peer aborts during the
            # handshake -- a port scanner does it. Treating that as shutdown would unbind
            # the port for the rest of the MD session.
            if listener.fileno() == -1:
                state["stop"] = True
            return
        connection.setblocking(False)
        state["peers"].append(
            {"socket": connection, "buffer": b"", "opened": time.monotonic()}
        )


def _poll_peer(peer, now):
    """One look at a peer: its request line, None to wait for more, or False to drop it."""
    try:
        chunk = peer["socket"].recv(65536)
    except (BlockingIOError, InterruptedError):
        chunk = None
    except OSError:
        return False
    if chunk == b"":
        # Went away before finishing a request. Nothing to reply to.
        return False
    if chunk:
        peer["buffer"] += chunk
    if b"\n" in peer["buffer"]:
        return peer["buffer"].split(b"\n", 1)[0]
    waited = now - peer["opened"]
    # Two deadlines: one for a peer that is talking and slow, a much shorter one for a
    # peer that has not said a word. Only the second one can be triggered by somebody
    # who is not a client at all.
    if waited > REQUEST_TIMEOUT or (not peer["buffer"] and waited > SILENT_PEER_TIMEOUT):
        return False
    return None


def _serve_peers(state):
    """Answer every peer whose request is complete, oldest first, one at a time."""
    for peer in list(state["peers"]):
        outcome = _poll_peer(peer, time.monotonic())
        if outcome is None:
            continue
        state["peers"].remove(peer)
        try:
            if outcome is not False:
                # Counted before the reply goes out, so a client that reads the count
                # straight after its answer sees its own request in it.
                state["requests"] += 1
                _reply_to(peer["socket"], outcome, state)
        finally:
            with contextlib.suppress(OSError):
                peer["socket"].close()
        if state["stop"]:
            return


def _tick(state):
    """What the timer calls, on MD's main thread: accept, read, answer, give it back.

    Never raises: this is called from a Windows callback, where an exception has
    nowhere useful to go. What went wrong is kept in ``state["last_error"]``.

    **Does nothing when it is already running.** An MD call made while a request is
    being served can pump MD's message queue -- a modal dialog does -- and the timer
    fires inside it. Serving there would run a second request in the middle of the
    first, and MD is not re-entrant, so the inner tick returns at once and the request
    waits for the outer one to finish. One request at a time executes inside MD, by
    this flag rather than by luck.
    """
    if state["busy"] or state["stopped"]:
        return
    state["busy"] = True
    try:
        state["ticks"] += 1
        _accept_waiting(state)
        _serve_peers(state)
    except Exception as exc:
        state["last_error"] = "{}: {}".format(type(exc).__name__, exc)
    finally:
        state["busy"] = False
    if state["stop"]:
        _halt(state, "a shutdown request")


# ------------------------------------------------------------------------- the timers


def _keep_alive(callback):
    table = getattr(builtins, _KEEP_ALIVE, None)
    if table is None:
        table = []
        setattr(builtins, _KEEP_ALIVE, table)
    table.append(callback)


class _WindowsTimer:
    """A user32 thread timer: Windows calls ``callback`` from MD's own message loop.

    Measured on 2026.0.315 (2026-10-11): armed from a Python Editor Run that then
    returned, a 25 ms timer fired on MD's main thread for as long as MD ran -- every
    31 ms or so, since Windows rounds up to its clock tick -- MD's API answered inside
    it, MD stayed responsive, and closing MD ended the process.

    No window: a timer with no window is delivered to the thread that set it, and that
    is MD's main thread, the one every Plugin tab click and Python Editor Run uses.
    """

    def __init__(self, interval_ms, callback):
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        # The id and the callback are pointer-sized. Left to ctypes' int-sized defaults
        # a 64-bit value is truncated, and Windows calls whatever the truncation names.
        proc_type = ctypes.WINFUNCTYPE(
            None, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, wintypes.DWORD
        )
        user32.SetTimer.argtypes = [
            wintypes.HWND, ctypes.c_size_t, wintypes.UINT, proc_type
        ]
        user32.SetTimer.restype = ctypes.c_size_t
        user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        user32.KillTimer.restype = wintypes.BOOL
        self._user32 = user32

        def fire(hwnd, message, event, when):
            # Nothing may escape into ctypes: it would print to a console nobody sees
            # and carry on, which hides the failure without containing it.
            try:
                callback()
            except BaseException:
                pass

        self._proc = proc_type(fire)
        _keep_alive(self._proc)
        self._id = user32.SetTimer(None, 0, interval_ms, self._proc)
        if not self._id:
            raise OSError("SetTimer failed ({})".format(ctypes.get_last_error()))

    def cancel(self):
        if self._id:
            self._user32.KillTimer(None, self._id)
            self._id = 0


class _ThreadTimer:
    """Calls ``callback`` every interval from a thread of its own. For the tests only.

    Inside MD this would be the lie the module docstring describes: MD freezes every
    Python thread the moment a script returns, so the port would bind and nothing
    would answer. The tests run on a real CPython that schedules threads -- and a
    Windows timer fires only for a thread that pumps messages, which a test runner
    does not -- so here a thread is the faithful stand-in.
    """

    def __init__(self, interval_ms, callback):
        self._stopped = threading.Event()

        def run():
            while not self._stopped.wait(interval_ms / 1000.0):
                callback()

        self._thread = threading.Thread(target=run, name="md-mcp-timer", daemon=True)
        self._thread.start()

    def cancel(self):
        self._stopped.set()


# -------------------------------------------------------------------------- lifecycle


def _bind(port):
    """Bind the port this session answers on. Raises if it cannot."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        # Stop then Start is two clicks here, and on POSIX the port sits in TIME_WAIT
        # for a minute or two after the old socket closes -- long enough that the Start
        # fails with EADDRINUSE and reads as "something else has the port". Not set on
        # Windows on purpose: there SO_REUSEADDR lets a second process steal a port
        # already bound, and Windows lets the rebind happen without it anyway.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((HOST, port))
        listener.listen(8)
    except OSError:
        with contextlib.suppress(OSError):
            listener.close()
        raise
    listener.setblocking(False)
    return listener


def _port_is_free(port):
    try:
        socket.create_connection((HOST, port), timeout=0.5).close()
    except OSError:
        return True
    return False


def _stop_foreign(port):
    """Ask whatever holds ``port`` to stand down, and wait for it to let go.

    For a listener this process does not know about: one left by a previous MD, or by an
    older version of this file still looping in this one. Binding with ``SO_REUSEADDR``
    instead would succeed on Windows while the old socket is still listening, and
    connections would land on whichever of the two the stack felt like.
    """
    if _port_is_free(port):
        return False
    try:
        with socket.create_connection((HOST, port), timeout=1.0) as client:
            client.sendall(b'{"op": "shutdown"}\n')
            client.recv(4096)
    except OSError:
        pass
    for _ in range(20):
        if _port_is_free(port):
            return True
        time.sleep(0.25)
    raise RuntimeError(
        "port {} is still in use and did not answer a shutdown; close whatever holds "
        "it, or set MD_MCP_PORT on both sides".format(port)
    )


def _halt(state, why):
    """Stop serving: kill the timer, close the port and every peer. Safe to repeat.

    Returns whether this call is the one that stopped it.
    """
    if state["stopped"]:
        return False
    state["stopped"] = True
    state["stop"] = True
    timer, state["timer"] = state["timer"], None
    if timer is not None:
        with contextlib.suppress(Exception):
            timer.cancel()
    listener, state["listener"] = state["listener"], None
    if listener is not None:
        with contextlib.suppress(OSError):
            listener.close()
    for peer in state["peers"]:
        with contextlib.suppress(OSError):
            peer["socket"].close()
    state["peers"] = []
    table = _registry()
    if table.get(state["port"]) is state:
        del table[state["port"]]
    print("md-mcp: stopped listening on {}:{} ({}).".format(HOST, state["port"], why))
    return True


def _default_timer():
    """The timer MD needs: Windows', since nothing else fires on MD's main thread."""
    if not hasattr(ctypes, "WinDLL"):
        raise RuntimeError("md-mcp serves from a Windows timer, and this is not Windows")
    return _WindowsTimer


def running(port=None):
    """The listener serving ``port`` in this process, or None."""
    state = _registry().get(PORT if port is None else port)
    if state is None or state.get("stopped", True):
        return None
    return state


def start(port=None, timer=None):
    """Open the port, arm the timer that serves it, and return its state. Raises if not.

    Returns at once -- inside MD that is the point: the Run that called this finishes,
    and the timer serves from MD's message loop until `stop`, a shutdown request, or
    MD closing.

    Called while a listener is already serving the port, it changes nothing and returns
    that listener's state: a second click on Start MCP must not drop what the first is
    serving.

    ``timer`` makes the timer; it defaults to the Windows one MD needs. The tests pass
    `_ThreadTimer`, for the reason its docstring gives.
    """
    port = PORT if port is None else port
    already = running(port)
    if already is not None:
        return already
    timer = timer or _default_timer()

    _stop_foreign(port)
    state = {
        "port": port,
        "listener": _bind(port),
        "timer": None,
        "peers": [],
        "busy": False,
        "stop": False,
        "stopped": False,
        "requests": 0,
        "ticks": 0,
        "last_error": "",
        "listener_path": _this_file(),
    }
    # What md_mcp_stop.py calls. It cannot import this file -- running it is Start --
    # so the way in is a function left where both runs can see it.
    state["halt"] = functools.partial(_halt, state)
    try:
        state["timer"] = timer(TICK_MS, functools.partial(_tick, state))
    except BaseException:
        with contextlib.suppress(OSError):
            state["listener"].close()
        raise
    _registry()[port] = state
    print(
        "md-mcp: listening on {}:{} (MD {}, plugin {})".format(
            HOST, port, _md_version(), __version__
        )
    )
    return state


def stop(port=None, why="stopped"):
    """Stop the listener serving ``port`` in this process. Returns whether one was."""
    state = running(port)
    return state is not None and _halt(state, why)


def _report(message):
    """Say something to whoever clicked, wherever MD will show it.

    Where a clicked plugin's ``print`` output goes has not been measured, so the
    outcome of a click goes in a message box too. It is modal, and that is fine here:
    the timer is already armed, and a modal box keeps dispatching it.
    """
    print("md-mcp: " + message)
    box = getattr(
        utility_api, "DisplayMessageBoxW", getattr(utility_api, "DisplayMessageBox", None)
    )
    if box is not None:
        with contextlib.suppress(Exception):
            box("md-mcp: " + message)


# --------------------------------------------------------------- the Plugin tab entries


def _this_file():
    """The path this listener was loaded from, or None if it was run without one.

    MD's Python Editor and the two-line bootstrap both set ``__file__``; an ``exec`` of
    the source text alone does not, and neither does a test that loads it by hand.
    """
    path = globals().get("__file__")
    return os.path.abspath(path) if path else None


def _state_dir():
    """Where this plugin keeps what it has to remember between MD sessions."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "md-mcp")


def _marker_path():
    """Where we record that MD has been told about each entry.

    Nothing here reads MD's plugin list -- `RegisterPythonScript` answers a bool and
    there is no reader for the list itself -- so "is it already registered?" is not a
    question MD can be asked, and this file answers it instead.
    """
    return os.path.join(_state_dir(), "registered.json")


def _read_marker(marker):
    try:
        with open(marker, encoding="utf-8") as handle:
            done = json.load(handle)
    except (OSError, ValueError):
        return {}
    return done if isinstance(done, dict) else {}


def _write_marker(marker, done):
    """Record what MD has been told. Returns a complaint, or None if it was recorded."""
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as handle:
            json.dump(done, handle, indent=2, sort_keys=True)
    except OSError as exc:
        return (
            "md-mcp: could not record the registration in {} ({}), so the next run "
            "will ask MD again".format(marker, exc)
        )
    return None


#: What MD shows in the Plugin tab: one entry that starts the bridge, one that stops it.
#: Exactly two, because MD's Plug-in Manager adds one `.py` at a time and MD has no call
#: that removes one: every entry a plugin owns is a thing somebody may have to add by
#: hand, and can only take back in Plug-in Manager.
START_ENTRY = "Start MCP"
STOP_ENTRY = "Stop MCP"

#: The file Stop MCP runs. It ships beside this one.
STOP_SCRIPT = "md_mcp_stop.py"


def register_plugin(listener=None, marker=None):
    """Put Start MCP and Stop MCP in MD's Plugin tab. Returns lines saying what happened.

    **Never raises.** This runs on the way to serving, and a bridge with no buttons is
    worth more than neither.

    Asks once per (path, name) rather than once per session: MD does not say whether it
    already had a script, so asking every time is how the Plugin tab fills up with
    duplicates that nothing can remove. Delete the marker file to ask again.

    **The path comes first: `RegisterPythonScript(path, title)`.** Measured on MD
    2026.0.315, 2026-10-11, from MD's own log
    (``C:\\Users\\Public\\Documents\\MarvelousDesigner\\New Assets\\api_plugin_log.txt``):
    called as (title, path) it reported "the script path is invalid: <MD's install
    folder>/Start MCP" -- the title, read as a relative path. Every refusal this file
    used to blame on running inside another script is explained by that. Called as
    (path, title) MD read the path, and declined a path it already had ("already
    registered"). It is declared `-> bool`, so the bool is checked and a False is never
    recorded: an entry written down as registered is one nobody ever offers again.
    """
    listener = os.path.abspath(listener) if listener else _this_file()
    if listener is None:
        return (
            "md-mcp: not registering anything -- this was run from source text with no "
            "__file__, so there is no path to give MD. Run the two-line bootstrap from "
            "the README instead of pasting the file's contents."
        )

    entries = [(START_ENTRY, listener)]
    stopper = os.path.join(os.path.dirname(listener), STOP_SCRIPT)
    lines = []
    if os.path.isfile(stopper):
        entries.append((STOP_ENTRY, stopper))
    else:
        lines.append(
            "md-mcp: no {} beside {}, so there is no Stop MCP entry to register; "
            "reinstall the plugin to get one.".format(STOP_SCRIPT, listener)
        )

    marker = marker or _marker_path()
    done = _read_marker(marker)
    # The wide form for the path: Windows user directories routinely contain non-ASCII
    # characters, and an install is under the user's own profile.
    register = getattr(
        utility_api, "RegisterPythonScriptW", utility_api.RegisterPythonScript
    )
    added = []
    for name, path in entries:
        key = "{} :: {}".format(os.path.normcase(path), name)
        if key in done:
            continue
        accepted, why = _ask_md(register, name, path)
        if not accepted:
            lines.append(
                "md-mcp: MD would not register {}. MD gives the reason in its plugin "
                "log -- C:\\Users\\Public\\Documents\\MarvelousDesigner\\New Assets\\"
                "api_plugin_log.txt -- and declines a script it already has, whatever "
                "the entry is called. To add it by hand: Plugin tab > Plug-in Manager > "
                "+ ADD > {}".format(why, path)
            )
            continue
        done[key] = {"name": name, "path": path, "plugin_version": __version__}
        added.append(name)

    if added:
        complaint = _write_marker(marker, done)
        lines.insert(0, "md-mcp: added {} to MD's Plugin tab.".format(
            " and ".join(repr(name) for name in added)))
        if complaint:
            lines.append(complaint)
    elif not lines:
        lines.append(
            "md-mcp: {} already in MD's Plugin tab (delete {} to ask MD again)".format(
                " and ".join(repr(name) for name, _ in entries), marker
            )
        )
    return "\n".join(lines)


def _ask_md(call, name, path):
    """One registration attempt. Returns (accepted, why not).

    MD answers a bool and says nothing else, so "why not" is only ever the name and,
    if it raised, what it raised.
    """
    try:
        # Path first, title second -- see register_plugin.
        accepted = call(path, name)
    except Exception as exc:
        return False, "{} ({}: {})".format(name, type(exc).__name__, exc)
    if accepted is False:
        return False, name
    return True, ""


def main():
    """What Start MCP runs: register both entries once, start serving, and return."""
    registered = ""
    if os.environ.get("MD_MCP_NO_REGISTER") != "1":
        registered = register_plugin()
        print(registered)
    was_running = running() is not None
    try:
        state = start()
    except (OSError, RuntimeError) as exc:
        _report("could not listen on {}:{}: {}".format(HOST, PORT, exc))
        return
    if was_running:
        message = "already listening on {}:{} ({} request(s) so far).".format(
            HOST, PORT, state["requests"]
        )
    else:
        message = "listening on {}:{}. Stop MCP in the Plugin tab stops it.".format(
            HOST, PORT
        )
    # A box only when something needs attention -- here, news about the entries. A
    # routine success is printed and nothing more: a modal box is a click every session,
    # and a dialog on screen is exactly what md-mcp warns about. md_status confirms the
    # bridge is up.
    if registered and "already in MD's Plugin tab" not in registered:
        _report(message + "\n\n" + registered.replace("md-mcp: ", ""))
    else:
        print("md-mcp: " + message)


#: Not ``if __name__ == "__main__"``. What MD sets ``__name__`` to for a script run
#: from the Plugin tab has not been measured, and a guard that insists on ``__main__``
#: would make the entry do nothing at all if MD sets anything else -- a button that
#: silently does nothing being the worst of the available failures. So the rule is
#: inverted: this runs unless something that loads it deliberately says not to, which
#: is what the tests do.
if globals().get("MD_MCP_AUTORUN", True):
    main()
