# md-mcp

**An MCP server that hands a model the whole of Marvelous Designer's Python API.**
`md_api` searches everything the running build exposes, `md_call` calls any of it,
`run_md_python` runs a script inside MD, `md_status` says whether MD is there at all.

Nothing is wrapped, curated or modelled. MD ships around 700 calls across six modules;
all of them are reachable, and this repo's job is only to carry them across the process
boundary faithfully and to fail legibly when it cannot.

The one addition is `src/md_mcp/notes.py`: MD's bindings ship no prose, so a measured
note rides along with a call's signature where one exists. It **annotates and never
replaces** — nothing is dropped, reordered or filtered, calls with nothing measured come
back untouched, and the empty-search index stays names only. A note whose key this build
does not expose fails a live test rather than silently never appearing.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest                  # 93 tests, no MD needed
.venv\Scripts\python.exe -m pytest -m md_required   # 42 more, with MD open
python tools\build_plugin.py                        # dist/md-mcp-plugin-<version>.zip
```

Register with an MCP client as the `md-mcp` command (`.venv\Scripts\md-mcp.exe`), and
the MD half with `plugin\install.ps1`.

## Layout

```
src/md_mcp/server.py   The four MCP tools.
src/md_mcp/bridge.py   md-mcp's end of the socket MD listens on.
plugin/                The other end: the listener that runs inside MD, and its installer.
tools/build_plugin.py  Packages the listener for MD: stamped, hashed, reproducible.
.github/workflows/     Tests on every push; a `v*` tag publishes the zip and the wheel.
docs/md-conventions.md How MD behaves. Measured against a real build, not assumed.
docs/adr/001-*.md      Why the transport is a socket MD hosts.
```

MD cannot be dialled into — this build has no `REST_API` module at all — so MD hosts the
socket and md-mcp connects to it. One JSON object per line each way. The serve loop runs
on **MD's own main thread** and pumps MD's message queue: MD only releases the GIL while
a script is running, so a daemon-thread listener binds its port and then freezes, and
every request times out against a bridge that looks connected. `md_mcp` never imports
MD's host modules, so it installs and tests on a machine with no Marvelous Designer;
`tests/test_no_md_imports.py` enforces that.

## Running the listener inside MD

If the plugin has been installed, click **md-mcp** in MD's Plugin tab: it opens the
control window and serves. Otherwise paste these two lines into **Main Menu → Plugins →
Python Editor**, then **Run**. Hand them over rather than the file's contents: MD keeps
its own editor buffer and it is easy to run a stale copy without noticing — and a pasted
body has no `__file__`, so it cannot register the entry either.

```python
path = r"C:\dev\marvelous-designer-plugins\plugin\md_mcp_listener.py"
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), {"__file__": path, "__name__": "__main__"})
```

`compile(..., path, ...)` puts the real filename in a traceback instead of `<string>`.
It opens the control window **idle** -- **Start** is what binds the port, because `run`
executes arbitrary Python inside MD and that takes a press, not a side effect of opening
a menu entry -- and then serves until MD closes or **Stop** is clicked. That run also registers
the file itself as the single Plugin tab entry, once per installed path. It never
raises; it reports what happened and serves regardless — including when the window will
not open, which is reported in a message box and stepped over.

The window is `user32` through `ctypes`, not a toolkit: MD has no Qt binding, and its
`tkinter` imports but cannot open anything, since MD ships Tk's DLLs without Tcl's
script library (`Tk()` → "Can't find a usable init.tcl", measured 2026-09-19).

**One loop does everything.** It pumps MD's message queue, refreshes the window and
accepts connections. A window with its own `mainloop()`, or a second loop anywhere,
would hold the only thread MD lets Python run on.

**Register from a top-level Run only.** `RegisterPythonScript` is declared `-> bool`, and
it answers `False` for every name and path when called from a script running inside
another one — measured on 2026.0.315, four attempts over the bridge, none of which
appeared, minutes after the same call worked from the Python Editor. So a registration
driven through `run_md_python` will not take, and `False` must never be recorded as
success: the entry would never be offered again. `plugin/README.md` has the GUI route.

**That Run never finishes, and must not.** The listener is the running script. MD stays
usable because the loop pumps MD's message queue; it is unresponsive only while a
request is actually executing. **Re-running the script stops the listener — run it once
more for a fresh one.** It cannot replace it in one run: the second run executes nested
inside the first one's loop, so the first cannot answer a shutdown or free its port
until the second returns.

## Tests

**A test you have not seen fail is not evidence.** Break what it covers, watch it fail
with a message that names the problem, restore. One probe at a time — two broken fixes
mask each other.

**Never mock past MD.** `tests/fake_md_host.py` runs the shipped listener against fake
host modules, which proves the protocol and nothing about Marvelous Designer. Live claims
need `pytest -m md_required` with MD open.

**Two live fixtures, and the difference matters.** `md` calls `NewProject` and so refuses
a scene with anything in it — which means that while real work is open, the tests taking
it all skip. `md_live` only proves MD answers, for claims that read or that touch nothing
but the clock; take it and the suite stays useful against a loaded scene, but a test that
builds, deletes or simulates under it will damage someone's work, because nothing here
restores anything.

## Things that will bite

**`md_call` pastes the function name into generated source.** It must stay an identifier
(`isidentifier()`), or a name is arbitrary code inside MD. The module whitelist alone is
not a check — a payload needs no dot to escape it.

**A wrong seam builds cleanly.** `AddSeamlinePairGroup` returns `False` for an index that
does not exist, but it validates indices, not meaning: a seam joining a hem to a neckline is accepted and
counted. Whatever you build, count what MD ended up with; the scene looking right is not
evidence. `md_status` and `run_md_python` report the seam groups, avatars and fabrics for
that reason.

**`Simulate(n)` runs n solver steps and returns `True` for any n.** `Simulate(1)` leaves
the garment flat and reports success. Steps accumulate across calls; a few hundred is a
drape. Measure the result, never the return value.

**A modal dialog inside MD stops the bridge dead.** It holds MD's main thread, which is
the thread serving requests, and nothing at MD's end times out — a call that never
returns is worth *looking at MD* before it is worth debugging. Sewing an edge to itself
is the measured way to summon one.

**MD keeps a registered plugin's path for good**, and nothing unregisters — there is no
remover in the six modules and no reader for the list either. An install from two
versions ago goes on answering, exactly as confidently as a current one, and every
symptom of that reads as md-mcp getting MD wrong. `ping` carries the listener's version,
commit and path, and `md_status` reports all three — check them before believing
anything else.

**A plugin clicked while a script is running executes nested inside it**, on MD's main
thread, and can read and change that script's state through `builtins`. That is what
makes a Stop button possible, and it is also why the clicked script must return promptly:
nothing below it moves until it does.

**An MD line runs from one straight point to the next**, absorbing every curve point
between them. A curve sampled at 50 points is one line, not 49.

**MD's API has no undo**, and `NewProject()` discards the scene without asking.

**Prefer the live build to any documentation, including `docs/md-conventions.md`.**
`md_api` reads the running host, which is the only ground truth. When a document turns out
to be wrong, fix it in the same commit as the code and say which MD version you measured.
