# md-mcp

**An MCP server that hands a model the whole of Marvelous Designer's Python API.**
`md_api` searches everything the running build exposes, `md_call` calls any of it,
`run_md_python` runs a script inside MD, `md_status` says whether MD is there at all.

The raw API stays whole: MD ships around 700 calls across six modules, all of them
reachable unwrapped, carried across the process boundary faithfully and failing legibly
when they cannot. On top of it sits a **construction layer** -- `md_draft_piece`,
`md_sew`, and placement to come -- that builds with those calls and then checks what MD
did. It may decide *how* to build (which point types, which line, which flags); it never
decides *what* to build. The garment is the caller's, and every construction tool reads
MD back rather than trusting its own plan.

The one addition is `src/md_mcp/notes.py`: MD's bindings ship no prose, so a measured
note rides along with a call's signature where one exists. It **annotates and never
replaces** — nothing is dropped, reordered or filtered, calls with nothing measured come
back untouched, and the empty-search index stays names only. A note whose key this build
does not expose fails a live test rather than silently never appearing.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest                  # no MD needed
.venv\Scripts\python.exe -m pytest -m md_required   # with MD open; most need it empty
python tools\build_plugin.py                        # dist/md-mcp-plugin-<version>.zip
python tools\build_mcpb.py                          # dist/md-mcp-<version>.mcpb (Windows)
```

Register with an MCP client as the `md-mcp` command (`.venv\Scripts\md-mcp.exe`), and
the MD half with `plugin\install.ps1`. Claude Desktop users get the `.mcpb` instead: it
bundles CPython's embeddable build, the locked wheels and the stamped plugin, and its
build refuses to finish until the unpacked bundle has started and listed its tools.

## Layout

```
src/md_mcp/server.py   The MCP tools: the raw API, and the tools that read MD back.
src/md_mcp/guide.md    What md_guide serves: measured facts about MD, kept short.
src/md_mcp/drafting.py Plans a piece from named edges and judges MD's read-back. No MD.
src/md_mcp/sewing.py   Plans one seam from edge names and confirms it in the ledger.
src/md_mcp/geometry.py OBJ meshes: exact body sections, landmarks, pieces registered to
                       their pattern through any texture mapping. No MD.
src/md_mcp/fit.py      Seams, pieces and rings checked in 3D against the body. No MD.
src/md_mcp/bridge.py   md-mcp's end of the socket MD listens on.
plugin/                The other end: the listener that runs inside MD, and its installer.
tools/build_plugin.py  Packages the listener for MD: stamped, hashed, reproducible.
tools/build_mcpb.py    The Claude Desktop bundle: server, its own Python, the plugin.
packaging/mcpb/        The bundle's dependency lock (hashed; --relock rewrites it) and
                       the MCPB manifest schema it is validated against.
.github/workflows/     Tests on every push; a `v*` tag publishes the zip and the wheel.
docs/md-conventions.md How MD behaves. Measured against a real build, not assumed.
docs/garment-preflight.md  Checks to run before and after changing a garment.
CHANGELOG.md           What each version changed, for users. Add to it with every change.
```

MD cannot be dialled into — this build has no `REST_API` module at all — so MD hosts the
socket and md-mcp connects to it. One JSON object per line each way. The listener is
served by a **Windows timer on MD's own main thread**: MD only releases the GIL while a
script is running, so a daemon-thread listener binds its port and then freezes, and
every request times out against a bridge that looks connected. `md_mcp` never imports
MD's host modules, so it installs and tests on a machine with no Marvelous Designer;
`tests/test_no_md_imports.py` enforces that.

## Running the listener inside MD

If the plugin has been installed, click **Start MCP** in MD's Plugin tab (an install from
before 2026-10-11 calls it **md-mcp**); a box appears only if it fails. **Stop MCP**
stops it. Otherwise paste these two lines into **Main Menu → Plugins → Python Editor**,
then **Run**, with `<checkout>` replaced by this repo's path. Hand them over rather than the file's contents: MD keeps its own editor
buffer and it is easy to run a stale copy without noticing — and a pasted body has no
`__file__`, so it cannot register the entries either.

```python
path = r"<checkout>\plugin\md_mcp_listener.py"
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), {"__file__": path, "__name__": "__main__"})
```

`compile(..., path, ...)` puts the real filename in a traceback instead of `<string>`.
That run binds the port, arms a 10 ms `user32.SetTimer` timer and **returns**; Windows
then calls the timer from MD's message loop, on MD's main thread, and each tick accepts,
reads and answers what is ready and gives the thread straight back. It also registers
Start MCP (this file) and Stop MCP (`md_mcp_stop.py` beside it) once per installed
path. Nothing listens until Start is clicked, because `run` executes arbitrary Python
inside MD. It serves until **Stop MCP**, a `shutdown` request, or MD closing — and
closing MD now ends the process, which the old never-returning listener did not.

**A tick never runs inside another.** An MD call that pumps the message queue — a modal
dialog does — lets the timer fire inside the request that made it; that inner tick
returns at once, so one request at a time executes inside MD. Stop MCP finds the running
listener on `builtins` (`__md_mcp_listeners__`), since it cannot import the listener
without starting it. There is no window: the old `user32` panel and the loop that drove
it were removed on 2026-10-11.

**`RegisterPythonScript` takes the path first: `(path, title)`.** For a month this repo
passed `(title, path)` and blamed the `False` on "running inside another script"; MD's
own plugin log (`C:\Users\Public\Documents\MarvelousDesigner\New Assets\api_plugin_log.txt`)
showed it reading the title as a path (measured 2026-10-11). It also declines a path it
already has, whatever the title. Read that log before theorising about a refusal, and
never record a `False` as success: the entry would never be offered again. The plugin
list itself is `…\MarvelousDesigner\Plugins\pluginSettings.json`, rewritten when MD exits.
`plugin/README.md` has the GUI route.

**After changing the listener, click Stop MCP and then Start MCP.** Start reads the file
afresh; a listener left running keeps the code it was loaded with.

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

**An OBJ's `vt` is not millimetres.** It is the pattern position only for MD's default
fabric; a textured fabric scales it, and unevenly for a twill (measured 2026-10-12).
`geometry.Piece.register_outline` fits the map; anything reading `vt` directly is
measuring the fabric's texture, and was the cause of a T-shirt reported with seams
150 mm open that had closed.

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
thread, and can read and change that script's state through `builtins` — which is how
Stop MCP reaches the listener. It is also why a clicked script must return promptly:
nothing below it moves until it does, and that includes the timer serving the bridge.

**An MD line runs from one straight point to the next**, absorbing every curve point
between them. A curve sampled at 50 points is one line, not 49.

**MD's API has no undo**, and `NewProject()` discards the scene without asking.

**Prefer the live build to any documentation, including `docs/md-conventions.md`.**
`md_api` reads the running host, which is the only ground truth. When a document turns out
to be wrong, fix it in the same commit as the code and say which MD version you measured.

## Before garment mutations

Read docs/garment-preflight.md for the recommended construction checks, and load only
the relevant measured sections of docs/md-conventions.md. Construction tools refuse what
MD would accept and build wrong, then read the result back -- `md_sew` checks the ledger
for exactly the seam it planned -- and none of them hides the raw API or chooses the
garment. A count proves neither correct sewing nor collision freedom.

**Edge names live in the garment's metadata** (`md_mcp.pieces`, via
`ChangeMetaDataValueForCurrentGarment`). `SetAPIMetaData` looks like the place and
returns False for every key (measured 2026-10-12).

Every run returns the complete available scene list. Do not reintroduce listener-global
suppression: API queries and independent MCP clients also consume run replies.
Execution failure and read-back failure are separate; preserve partial output/state.
