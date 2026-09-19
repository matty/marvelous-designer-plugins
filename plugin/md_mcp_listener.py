"""md-mcp listener — the live bridge, run inside Marvelous Designer.

One file, one entry in MD's Plugin tab, one small window with Start and Stop in it.

Nothing is imported that MD does not already have. The window is built on `user32`
through `ctypes` -- the same DLL this file pumps MD's message queue with. MD's own UI is
Qt and its three plugin-UI hooks are all out of reach from Python (see
`docs/md-conventions.md`); a matching PySide6 *can* be installed into MD's interpreter
and does bind to MD's Qt, but stock Qt widgets in MD's palette still do not look like
MD, and it is not worth ninety megabytes inside MD to find that out again.

Click **md-mcp** in the Plugin tab if it has been registered there, or run the two-line
bootstrap from the README in **Main Menu > Plugins > Python Editor**. Either opens the
control window; **Start** is what opens the port. The first top-level run also registers
the entry, so every session after it is a click (see `register_plugin`).

Opening the plugin does not put a port on the network. `run` executes arbitrary Python
inside MD, so that wants a deliberate press rather than a side effect of curiosity about
a menu entry. Where there is no window to press -- it could not be built -- the bridge
opens itself and says so, since otherwise the plugin would do nothing at all.

Closing the window does not stop the bridge -- it is a control panel, not a leash.
Clicking **md-mcp** again brings it back.

Why the loop is on MD's main thread
-----------------------------------
MD embeds CPython and only lets it run while a script is executing. The moment a script
returns, the host stops releasing the GIL and **every Python thread freezes** — measured
on 2026.0.315: a thread ticked 20 times while the main thread slept 2s, and not once
after the script ended. A listener on a background thread therefore binds its port (so
connections are accepted into the backlog and `connect()` succeeds) and then answers
nothing, which is the worst failure shape available: it looks connected and is dead.

So the serve loop *is* the script, and holds MD's main thread for the whole session. To
keep MD usable it pumps MD's own Win32 message queue between requests — without that,
Windows marks MD "not responding" after about four seconds (both halves measured; see
`docs/adr/001-bridge-transport.md`). Every MD API call then happens on the main thread,
which is also the only thread MD documents as safe.

While a request is executing, the pump is not running, so MD is unresponsive for exactly
as long as the work takes — the same as clicking Simulate in the UI.

Protocol: one JSON object per line in, one JSON object per line out, then the connection
closes.

    {"op": "ping"}                       -> {"ok": true, "md_version": "...", ...}
    {"op": "scene"}                      -> {"ok": true, "pattern_count": n, "pieces": []}
    {"op": "run", "source": "...python"} -> {"ok": true, "output": "...", + scene}
    {"op": "shutdown"}                   -> {"ok": true}

Loopback only, and no authentication: anything that can reach 127.0.0.1 on this port can
run Python inside MD. That is the same trust boundary as MD's own script menu.
"""

import builtins
import contextlib
import ctypes
import io
import json
import os
import socket
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

#: How long a peer that has sent *nothing at all* is given before it is dropped. The loop
#: is serial, so a peer that connects and then says nothing -- a port scanner, a stray
#: browser tab -- holds the bridge for exactly this long, and giving it the whole
#: REQUEST_TIMEOUT made that hold as long as the client's own ping timeout: the next real
#: request would time out rather than queue. A client sends immediately after connecting
#: (md_mcp.bridge does it in the next statement), so a second is already generous.
SILENT_PEER_TIMEOUT = 1.0

#: How long the loop waits for a connection before pumping MD's message queue again.
#: Short enough that MD's UI stays smooth, long enough that idling costs nothing.
POLL_INTERVAL = 0.005

#: Listener state lives on ``builtins`` so that a second run of this script finds the
#: first one even though MD gives each run a fresh namespace. Keyed by port, because the
#: tests run several listeners at once.
_REGISTRY = "__md_mcp_listeners__"


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
    count = pattern_api.GetPatternCount()
    scene = {
        "pattern_count": count,
        "pieces": [
            {"index": index, "name": pattern_api.GetPatternPieceName(index)}
            for index in range(count)
        ],
    }
    _readback(scene, "seam_group_count", lambda: pattern_api.GetSeamlinePairGroupCount())
    _readback(scene, "avatars", lambda: list(__import__("export_api").GetAvatarNameList()))
    # Not GetFabricCount(), which answers 0 for a scene that plainly has a fabric in it
    # (measured on 2026.0.315). The style list is the one that tells the truth.
    _readback(
        scene, "fabrics", lambda: list(__import__("fabric_api").GetFabricStyleNameList())
    )
    return scene


# --------------------------------------------------------------------------- the pump


def _make_pump():
    """A callable that drains this thread's Win32 message queue.

    Returns False once MD has asked to quit, so the loop can let go of the main thread
    and let MD shut down: our frame is *below* MD's event loop, and MD cannot finish
    exiting until this script returns. Off Windows (the tests) there is no queue and no
    main thread to block, so pumping is a no-op that never asks anyone to stop.
    """
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except (AttributeError, OSError):
        return lambda: True

    from ctypes import wintypes

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", wintypes.POINT),
        ]

    # Declared rather than left to ctypes' int-sized default: the handle and the
    # W/LPARAMs are 64-bit here, and a truncated pointer dispatches into nothing.
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]

    PM_REMOVE = 0x0001
    WM_QUIT = 0x0012
    message = MSG()

    def pump():
        while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE):
            if message.message == WM_QUIT:
                # Put it back. MD's own loop resumes the moment we return and has to
                # see the quit, or closing MD would leave the process running headless.
                user32.PostQuitMessage(int(message.wParam))
                return False
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        return True

    return pump


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


def _run(source, name):
    """Execute a script in a namespace of its own, capturing everything it printed.

    ``sys.stdout`` is redirected rather than ``print`` rebound, so that output from
    anything the script *calls* is captured too. That is safe here only because this
    runs on MD's main thread with the pump stopped: nothing else in the process can
    write to the stream while it is swapped out.
    """
    captured = io.StringIO()
    namespace = _script_namespace(name)
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        exec(compile(source, name or "<md-mcp>", "exec"), namespace)
    return captured.getvalue()


def _collapse_unchanged(scene, state):
    """Replace the piece list with a marker when it has not moved since last time.

    Every ``run`` reply carries the scene so that what MD ended up with is checked
    rather than assumed, and that stays true: ``pattern_count`` and
    ``seam_group_count`` -- the numbers worth checking -- are scalars and always go.
    What does not need repeating is the list of names behind them. Measured on a
    58-piece garment: a run whose output was two characters returned 2,770, of which
    2,500 were a piece list identical to the one in the reply before it.

    So the list goes in full the first time and every time it differs, and a caller
    that never sees it change never sees it again. A changed list is never suppressed,
    which is the property that matters: silence here means "the same as the one you
    already have", never "nothing worth mentioning".
    """
    pieces = scene.get("pieces")
    if not isinstance(pieces, list):
        return scene
    key = json.dumps(pieces, sort_keys=True, default=repr)
    if key == state.get("last_pieces"):
        scene["pieces"] = "unchanged: same {} pieces as the previous reply".format(
            len(pieces)
        )
        scene["pieces_unchanged"] = True
    else:
        state["last_pieces"] = key
    return scene


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
        output = _run(source, request.get("name"))
        return dict(ok=True, output=output, **_collapse_unchanged(_scene(), state))
    if op == "shutdown":
        state["stop"] = True
        return {"ok": True, "stopped": True}
    return {"ok": False, "error": "unknown op {!r}".format(op)}


def _read_request(connection, pump):
    """Read one line from a connected peer, pumping MD while we wait for it."""
    deadline = time.time() + REQUEST_TIMEOUT
    first_byte_deadline = time.time() + SILENT_PEER_TIMEOUT
    buffer = b""
    connection.setblocking(False)
    while b"\n" not in buffer:
        now = time.time()
        # Two deadlines: one for a peer that is talking and slow, a much shorter one for
        # a peer that has not said a word. Only the second one can be triggered by
        # somebody who is not a client at all.
        if now > deadline or (not buffer and now > first_byte_deadline):
            return None
        try:
            chunk = connection.recv(65536)
        except BlockingIOError:
            pump()
            time.sleep(POLL_INTERVAL)
            continue
        except OSError:
            return None
        if not chunk:
            return None
        buffer += chunk
    connection.setblocking(True)
    return buffer.split(b"\n", 1)[0]


def _serve_connection(connection, state, pump):
    raw = _read_request(connection, pump)
    if raw is None:
        # Never sent a request, or went away mid-way. Nothing to reply to.
        return

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
        connection.sendall((json.dumps(reply) + "\n").encode("utf-8"))


# ----------------------------------------------------------------------------- the loop


def _bind(port):
    """Bind the port this session answers on. Raises if it cannot."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        # Stop then Start is a button here, and on POSIX the port sits in TIME_WAIT for
        # a minute or two after the old socket closes -- long enough that the Start
        # fails with EADDRINUSE and reads as "something else has the port". Not set on
        # Windows on purpose: there SO_REUSEADDR lets a second process steal a port
        # already bound, and Windows lets the rebind happen without it anyway.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((HOST, port))
        listener.listen(4)
    except OSError:
        with contextlib.suppress(OSError):
            listener.close()
        raise
    listener.setblocking(False)
    return listener


def start_serving(state, raising=False):
    """Open the port. Returns whether it is now serving.

    Swallows the failure by default, because this is what a button calls: an exception
    from a window callback would take the window and the loop with it, and "something
    else already has port 5121" is exactly the kind of thing the window exists to show.
    """
    if state["serving"]:
        return True
    port = state["port"]
    try:
        _stop_foreign(port)
        state["listener"] = _bind(port)
    except (OSError, RuntimeError) as exc:
        state["last"] = "could not listen on {}: {}".format(port, exc)
        print("md-mcp: " + state["last"])
        if raising:
            raise
        return False
    state["serving"] = True
    state["last"] = "listening on {}:{}".format(HOST, port)
    print(
        "md-mcp: listening on {}:{} (MD {}, plugin {})".format(
            HOST, port, _md_version(), __version__
        )
    )
    return True


def stop_serving(state):
    """Close the port and keep the loop -- and the window -- alive."""
    if not state["serving"]:
        return
    state["serving"] = False
    listener, state["listener"] = state["listener"], None
    with contextlib.suppress(OSError):
        listener.close()
    state["last"] = "stopped listening on {}:{}".format(HOST, state["port"])
    print("md-mcp: " + state["last"])


def _report(message):
    """Say something nobody should miss, wherever MD will show it.

    Where a clicked plugin's ``print`` output goes has not been measured, so anything
    that decides whether the plugin is usable at all goes in a message box too. It is
    modal, and it holds the thread this loop runs on until it is dismissed -- which is
    why this is for the window that would not open, and not for anything routine.
    """
    print("md-mcp: " + message)
    box = getattr(
        utility_api, "DisplayMessageBoxW", getattr(utility_api, "DisplayMessageBox", None)
    )
    if box is not None:
        with contextlib.suppress(Exception):
            box("md-mcp: " + message)


def _tick_panel(state, panel_factory):
    """Open, raise, refresh or forget the control window. Never raises."""
    if panel_factory is not None and state["show_panel"] and state["panel"] is None:
        state["show_panel"] = False
        state["panel"] = panel_factory(state)

    panel = state["panel"]
    if panel is None:
        return
    if state["show_panel"]:
        state["show_panel"] = False
        with contextlib.suppress(Exception):
            panel.raise_window()
    try:
        alive = panel.tick()
    except Exception as exc:
        # A window that broke must not take the bridge with it: the socket is the
        # part something else is depending on.
        state["last"] = "the control window stopped ({}: {})".format(
            type(exc).__name__, exc
        )
        print("md-mcp: " + state["last"])
        alive = False
    if not alive:
        state["panel"] = None


def _accept_one(state, pump):
    """Serve one waiting connection, if there is one. Returns whether there was."""
    try:
        connection, _ = state["listener"].accept()
    except (BlockingIOError, socket.timeout):
        return False
    except OSError:
        # Windows raises WSAECONNRESET from accept() when a peer aborts during the
        # handshake -- a port scanner does it. Treating that as shutdown would unbind
        # the port for the rest of the MD session.
        listener = state["listener"]
        if listener is None or listener.fileno() == -1:
            stop_serving(state)
        return False
    try:
        _serve_connection(connection, state, pump)
        state["requests"] += 1
    finally:
        with contextlib.suppress(OSError):
            connection.close()
    return True


def _loop(state, pump, panel_factory=None):
    """The loop that owns MD's main thread: the window, the socket, and MD's queue.

    One loop for all three on purpose. MD runs Python only while a script is running,
    so everything this plugin does has to happen inside one script that does not
    return -- a window with a ``mainloop()`` of its own would hold the thread and the
    bridge would answer nothing for as long as it was open.
    """
    while not state["stop"]:
        if not pump():
            # MD is quitting. Let go of the main thread so it can.
            break
        _tick_panel(state, panel_factory)
        if state["panel_error"] and not state["panel_reported"]:
            # Said once, and only once: this runs a few hundred times a second.
            state["panel_reported"] = True
            if state["serving"]:
                _report(state["panel_error"])
            else:
                # Waiting for a Start nobody can press would make this plugin do
                # nothing whatever. The bridge opens itself instead -- announced,
                # because it is not what the click asked for.
                _report(
                    state["panel_error"] + " Listening straight away instead, since "
                    "there is no Start button to press; run this plugin again to stop."
                )
                start_serving(state)
                continue
        if state["serving"]:
            if _accept_one(state, pump):
                continue
        elif state["panel"] is None:
            # Not listening and nothing on screen. There is nothing left for this
            # script to do, and a script with nothing to do must return: holding MD's
            # main thread costs MD and buys nobody anything.
            break
        time.sleep(POLL_INTERVAL)


# -------------------------------------------------------------------- the control window


#: Window and control styles, and the messages this panel answers. Plain integers, so
#: they are safe to define on a machine that has no user32 at all -- the tests run on
#: Linux, where everything below that touches ctypes.WinDLL is never reached.
_WS_CAPTION = 0x00C00000
_WS_SYSMENU = 0x00080000
_WS_MINIMIZEBOX = 0x00020000
_WS_CHILD = 0x40000000
_WS_VISIBLE = 0x10000000
_SS_LEFT = 0x00000000
_BS_PUSHBUTTON = 0x00000000
_CW_USEDEFAULT = -2147483648  # 0x80000000 as the signed int CreateWindowExW takes
_SW_SHOWNORMAL = 1
_SW_RESTORE = 9
_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_SETFONT = 0x0030
_WM_COMMAND = 0x0111
_DEFAULT_GUI_FONT = 17
_COLOR_BTNFACE = 15
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_IDC_ARROW = 32512

#: Ids for the two buttons, as WM_COMMAND reports them in the low word of wParam.
_IDC_START = 1001
_IDC_STOP = 1002

#: Counts the windows this process has built, so each gets a window class of its own.
#: Registering a class name that already exists fails and leaves the *first*
#: registration's WNDPROC in place -- a pointer into a Python callback that may since
#: have been collected, which Windows would then call. A fresh name costs nothing.
_PANEL_SEQUENCE = "__md_mcp_panel_sequence__"


class _NativePanel:
    """A small window built straight on user32: two buttons and a status line.

    No toolkit, on purpose. MD ships `_tkinter.pyd` with `tcl86t.dll` and `tk86t.dll`
    but none of Tcl's script library, so `Tk()` raises "Can't find a usable init.tcl"
    (measured on 2026.0.315, 2026-09-19) -- and shipping somebody else's Tcl to fix
    that is a dependency this plugin has no business carrying. `ctypes` and `user32`
    are already here for MD's message pump.

    It has no message loop of its own. The serve loop pumps this thread's queue, which
    is what dispatches clicks into `_handle_message`, and calls `tick` to refresh the
    labels. A loop or a modal dialog of its own would hold MD's main thread -- the one
    thread the bridge is served on.
    """

    def __init__(self, state):
        from ctypes import wintypes

        self._state = state
        self._open = False
        self._headline = None
        self._detail = None

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # LRESULT and the W/LPARAMs are pointer-sized. Left to ctypes' int-sized
        # default they are truncated, and a window procedure that returns a truncated
        # value is a window that misbehaves in ways nothing reports.
        wndproc = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32, gdi32 = self._user32, self._gdi32
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            ctypes.c_void_p,
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UpdateWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.LoadCursorW.restype = wintypes.HANDLE
        gdi32.GetStockObject.argtypes = [ctypes.c_int]
        gdi32.GetStockObject.restype = ctypes.c_void_p
        gdi32.CreateFontW.restype = ctypes.c_void_p
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        user32.AdjustWindowRect.argtypes = [
            ctypes.POINTER(wintypes.RECT),
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        table = _registry()
        sequence = table.get(_PANEL_SEQUENCE, 0) + 1
        table[_PANEL_SEQUENCE] = sequence

        # Both kept on the instance: a ctypes callback is collected like any other
        # object, and Windows holds this pointer for as long as the class is
        # registered.
        self._wndproc = wndproc(self._handle_message)
        self._class = WNDCLASSW()
        self._class.style = 0
        self._class.lpfnWndProc = self._wndproc
        self._class.hInstance = kernel32.GetModuleHandleW(None)
        self._class.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(_IDC_ARROW))
        self._class.hbrBackground = _COLOR_BTNFACE + 1
        self._class.lpszClassName = "md_mcp_panel_{}".format(sequence)

        if not user32.RegisterClassW(ctypes.byref(self._class)):
            raise OSError("RegisterClassW failed ({})".format(ctypes.get_last_error()))

        style = _WS_CAPTION | _WS_SYSMENU | _WS_MINIMIZEBOX
        self._hwnd = user32.CreateWindowExW(
            0,
            self._class.lpszClassName,
            "md-mcp",
            style,
            _CW_USEDEFAULT,
            _CW_USEDEFAULT,
            10,
            10,
            None,
            None,
            self._class.hInstance,
            None,
        )
        if not self._hwnd:
            raise OSError("CreateWindowExW failed ({})".format(ctypes.get_last_error()))
        self._open = True

        # Everything below is in 96-dpi units and scaled here. Laid out in raw pixels
        # it comes out too small for its own text on any display that is not at 100%,
        # and what it loses first is the right-hand end of the path -- the one line
        # that says *which* install is answering.
        scale = self._dpi_scale(user32, wintypes)

        def at(value):
            return int(round(value * scale))

        # The frame is not part of the client area, so the window has to be told the
        # size that *contains* the layout rather than the size of the layout.
        wanted = wintypes.RECT(0, 0, at(380), at(152))
        user32.AdjustWindowRect(ctypes.byref(wanted), style, False)
        user32.SetWindowPos(
            self._hwnd,
            None,
            0,
            0,
            wanted.right - wanted.left,
            wanted.bottom - wanted.top,
            _SWP_NOMOVE | _SWP_NOZORDER,
        )

        # The stock GUI font is a fixed 8pt that ignores the display entirely.
        self._font = gdi32.CreateFontW(
            -int(round(9 * scale * 96 / 72.0)), 0, 0, 0, 400,
            0, 0, 0, 0, 0, 0, 0, 0, "Segoe UI",
        )
        self._bold = gdi32.CreateFontW(
            -int(round(9 * scale * 96 / 72.0)), 0, 0, 0, 700,
            0, 0, 0, 0, 0, 0, 0, 0, "Segoe UI",
        )

        self._headline_label = self._child(
            "STATIC", at(14), at(12), at(352), at(20), _SS_LEFT, font=self._bold
        )
        self._detail_label = self._child(
            "STATIC", at(14), at(36), at(352), at(56), _SS_LEFT
        )
        self._start_button = self._child(
            "BUTTON", at(14), at(104), at(108), at(30), _BS_PUSHBUTTON, _IDC_START,
            "Start",
        )
        self._stop_button = self._child(
            "BUTTON", at(132), at(104), at(108), at(30), _BS_PUSHBUTTON, _IDC_STOP,
            "Stop",
        )

        user32.ShowWindow(self._hwnd, _SW_SHOWNORMAL)
        user32.UpdateWindow(self._hwnd)

    def _dpi_scale(self, user32, wintypes):
        """How much bigger than 96 dpi this window's display is.

        GetDpiForWindow is Windows 10 1607 and later; older Windows, and a process
        that is not dpi-aware, both answer 96 one way or another, which is the right
        answer for them -- Windows stretches the result instead.
        """
        try:
            user32.GetDpiForWindow.argtypes = [wintypes.HWND]
            user32.GetDpiForWindow.restype = wintypes.UINT
            dpi = user32.GetDpiForWindow(self._hwnd)
        except (AttributeError, OSError):
            return 1.0
        return (dpi or 96) / 96.0

    def _child(self, kind, x, y, width, height, style, control_id=0, text="", font=None):
        handle = self._user32.CreateWindowExW(
            0,
            kind,
            text,
            _WS_CHILD | _WS_VISIBLE | style,
            x,
            y,
            width,
            height,
            self._hwnd,
            ctypes.c_void_p(control_id),
            self._class.hInstance,
            None,
        )
        if not handle:
            raise OSError(
                "CreateWindowExW({}) failed ({})".format(kind, ctypes.get_last_error())
            )
        # Without this, every control comes up in the 1990s bitmap system font.
        self._user32.SendMessageW(handle, _WM_SETFONT, font or self._font, 1)
        return handle

    def _handle_message(self, hwnd, message, wparam, lparam):
        """The window procedure: runs inside the serve loop's pump, on MD's main thread.

        So a click changes the state directly -- there is nothing else running to race
        with, and by the time this returns the loop is already looking at the result.
        """
        if message == _WM_COMMAND:
            control = wparam & 0xFFFF
            if control == _IDC_START:
                start_serving(self._state)
                return 0
            if control == _IDC_STOP:
                stop_serving(self._state)
                return 0
        elif message == _WM_CLOSE:
            # Closing the window does not stop the bridge -- this is a control panel,
            # not a leash. Clicking md-mcp in the Plugin tab puts it back.
            self._user32.DestroyWindow(hwnd)
            return 0
        elif message == _WM_DESTROY:
            self._open = False
            return 0
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def tick(self):
        if not self._open:
            return False
        state = self._state
        headline = (
            "Listening on {}:{}".format(HOST, state["port"])
            if state["serving"]
            else "Not listening"
        )
        detail = "md-mcp {} - {} request(s)\n{}\n{}".format(
            __version__,
            state["requests"],
            state["last"] or "idle",
            _short_path(state["listener_path"]),
        )
        # Only when it changes: SetWindowTextW repaints, and this is called every few
        # milliseconds for the whole MD session.
        if headline != self._headline:
            self._headline = headline
            self._user32.SetWindowTextW(self._headline_label, headline)
        if detail != self._detail:
            self._detail = detail
            self._user32.SetWindowTextW(self._detail_label, detail)
        self._user32.EnableWindow(self._start_button, not state["serving"])
        self._user32.EnableWindow(self._stop_button, bool(state["serving"]))
        return True

    def raise_window(self):
        self._user32.ShowWindow(self._hwnd, _SW_RESTORE)
        self._user32.SetForegroundWindow(self._hwnd)

    def close(self):
        if self._open:
            self._open = False
            with contextlib.suppress(Exception):
                self._user32.DestroyWindow(self._hwnd)
        for font in ("_font", "_bold"):
            handle = getattr(self, font, None)
            if handle:
                setattr(self, font, None)
                with contextlib.suppress(Exception):
                    self._gdi32.DeleteObject(handle)


def _short_path(path):
    """The last two parts of a path, which is what identifies an install.

    The window is 380 units wide and a real install path is not. The whole path is in
    every `ping` for anyone who needs it; here it only has to answer "which one".
    """
    if not path:
        return ""
    parts = path.replace("/", "\\").split("\\")
    return path if len(parts) <= 2 else "...\\" + "\\".join(parts[-2:])


def _panel(state):
    """Build the control window, or record why there is none. Never raises.

    A missing window is a worse plugin, not a broken one: the bridge is the half
    something else depends on, so a window that will not open is reported and stepped
    over.
    """
    if not hasattr(ctypes, "WinDLL"):
        state["panel_error"] = (
            "no control window -- this is not Windows. The bridge is running anyway."
        )
        return None
    try:
        return _NativePanel(state)
    except Exception as exc:
        state["panel_error"] = (
            "no control window ({}: {}). The bridge is running anyway; run this "
            "plugin again to stop it.".format(type(exc).__name__, exc)
        )
        return None


# --------------------------------------------------------------- the Plugin tab entry


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
    """Where we record that MD has been told about this file.

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


#: What MD shows in the Plugin tab. **One** entry, pointing at this file: clicking it
#: opens the control window, and the window is where Start and Stop live. MD's Plug-in
#: Manager adds one `.py` at a time and MD has no call that removes one, so every entry
#: a plugin owns is a thing somebody has to add and can never take back.
PLUGIN_NAME = "md-mcp"


def register_plugin(listener=None, marker=None, name=PLUGIN_NAME):
    """Put this file in MD's Plugin tab. Returns a line saying what happened.

    **Never raises.** This runs on the way to serving, and a bridge with no button is
    worth more than neither.

    Asks once per (path, name) rather than once per session: MD does not say whether it
    already had a script, so asking every time is how the Plugin tab fills up with
    duplicates that nothing can remove. Delete the marker file to ask again.

    Measured on MD 2026.0.315, 2026-09-19. `RegisterPythonScript(name, path)` adds a
    working entry, without restarting MD -- from a **top-level Run** in the Python
    Editor. It is declared `-> bool` and it answers **False** for every name and path
    when called from a script that is itself running inside another one, which is what
    a run over the bridge is. So the bool is checked and a False is never recorded: an
    entry written down as registered is one nobody ever offers again.
    """
    listener = os.path.abspath(listener) if listener else _this_file()
    if listener is None:
        return (
            "md-mcp: not registering anything -- this was run from source text with no "
            "__file__, so there is no path to give MD. Run the two-line bootstrap from "
            "the README instead of pasting the file's contents."
        )

    marker = marker or _marker_path()
    done = _read_marker(marker)
    key = "{} :: {}".format(os.path.normcase(listener), name)
    if key in done:
        return "md-mcp: already in MD's Plugin tab as {!r} (delete {} to ask MD " "again)".format(
            name, marker
        )

    # The wide form for the path: Windows user directories routinely contain non-ASCII
    # characters, and this one is under the user's own profile.
    register = getattr(
        utility_api, "RegisterPythonScriptW", utility_api.RegisterPythonScript
    )
    accepted, why = _ask_md(register, name, listener)
    if not accepted:
        return (
            "md-mcp: MD would not register {}. It answers False for every name and "
            "path when the call comes from a script running inside another one "
            "(measured on 2026.0.315) -- so run the two-line bootstrap from the Python "
            "Editor with nothing else going. Failing that, add it by hand: Plugin tab "
            "> Plug-in Manager > + ADD > {}".format(why, listener)
        )

    done[key] = {"name": name, "path": listener, "plugin_version": __version__}
    complaint = _write_marker(marker, done)
    added = "md-mcp: added to MD's Plugin tab as {!r}. Click it there to open the " "control window.".format(
        name
    )
    return added + ("\n" + complaint if complaint else "")


def _ask_md(call, name, path):
    """One registration attempt. Returns (accepted, why not).

    MD answers a bool and says nothing else, so "why not" is only ever the name and,
    if it raised, what it raised.
    """
    try:
        accepted = call(name, path)
    except Exception as exc:
        return False, "{} ({}: {})".format(name, type(exc).__name__, exc)
    if accepted is False:
        return False, name
    return True, ""


# -------------------------------------------------------------------------- lifecycle


def _port_is_free(port):
    try:
        socket.create_connection((HOST, port), timeout=0.5).close()
    except OSError:
        return True
    return False


def _stop_foreign(port):
    """Ask whatever holds ``port`` to stand down, and wait for it to let go.

    This is for a listener left by a *previous* MD process. One left by this process
    cannot be reached this way -- see `start`. Binding with ``SO_REUSEADDR`` instead
    would succeed on Windows while the old socket is still listening, and connections
    would land on whichever of the two the stack felt like.
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


def start(port=None, block=True, serving=None, panel_factory=None):
    """Hold MD's main thread: show the window, and serve the bridge when told to.

    Returns when the bridge has been stopped and the window closed -- inside MD, that
    is the whole session.

    ``serving`` decides whether the port is open before anyone has pressed anything.
    It defaults to **whether there is no window**: with a window, opening the plugin
    opens the window and nothing else, and Start is what opens the port; without one,
    there would be nothing to press, so the bridge opens itself as it always did.

    ``panel_factory`` builds the control window from the loop's state; ``None`` means
    no window at all, which is what the tests use and what MD gets when the window
    cannot be built.

    Run a second time in the same MD, this does **not** replace the listener. The
    second run executes nested inside the first one's loop, so the first cannot answer
    a shutdown or release its port until the second returns; all a second run can do is
    leave a flag. With a window that flag is "show yourself"; without one it is "stop",
    and it says which.

    ``block=False`` serves on a background thread, which is a lie inside MD -- the
    thread freezes the moment this returns -- and is only for the tests, which run on a
    real CPython that schedules threads.
    """
    port = PORT if port is None else port
    table = _registry()

    running = table.get(port)
    if running is not None and not running["stop"]:
        if panel_factory is not None and not running.get("panel_error"):
            running["show_panel"] = True
            print("md-mcp: already running -- bringing the control window back.")
            return None
        running["stop"] = True
        print(
            "md-mcp: asked the listener on port {} to stop. It stops when this script "
            "returns; run it once more to start a fresh one.".format(port)
        )
        return None

    if serving is None:
        serving = panel_factory is None
    state = {
        "stop": False,
        "serving": False,
        "listener": None,
        "port": port,
        "panel": None,
        "show_panel": panel_factory is not None,
        "panel_error": "",
        "panel_reported": False,
        "requests": 0,
        "last": "" if serving else "press Start to listen",
        "listener_path": _this_file(),
    }
    table[port] = state

    if serving:
        # With a window to report into, a port that is taken is something to show and
        # recover from with a button. Without one it stays what it has always been: a
        # refusal, loudly, rather than a script that sits there looking started.
        start_serving(state, raising=panel_factory is None)
    else:
        print(
            "md-mcp {}: control window open on {}. Press Start to listen on "
            "{}:{}.".format(__version__, _md_version(), HOST, port)
        )

    pump = _make_pump()

    def run():
        try:
            _loop(state, pump, panel_factory)
        finally:
            state["stop"] = True
            if table.get(port) is state:
                del table[port]
            stop_serving(state)
            panel = state["panel"]
            state["panel"] = None
            if panel is not None:
                with contextlib.suppress(Exception):
                    panel.close()

    if not block:
        import threading

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    run()
    print("md-mcp: stopped.")
    return None


def main():
    """What Marvelous Designer runs: register the entry, open the window, serve."""
    if os.environ.get("MD_MCP_NO_REGISTER") != "1":
        print(register_plugin())
    start(panel_factory=_panel)


#: Not ``if __name__ == "__main__"``. What MD sets ``__name__`` to for a script run
#: from the Plugin tab has not been measured, and a guard that insists on ``__main__``
#: would make the entry do nothing at all if MD sets anything else -- a button that
#: silently does nothing being the worst of the available failures. So the rule is
#: inverted: this runs unless something that loads it deliberately says not to, which
#: is what the tests do.
if globals().get("MD_MCP_AUTORUN", True):
    main()
