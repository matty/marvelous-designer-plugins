# md-mcp

An MCP server that gives a model the whole of a running Marvelous Designer's Python API —
688 calls across six modules, none of them wrapped, curated or modelled.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

Register `.venv\Scripts\md-mcp.exe` as an MCP server with your client, then install the
MD half — from a release, unzip and run `install.ps1`; from this checkout:

```powershell
.\plugin\install.ps1          # copies the listener to %LOCALAPPDATA%\md-mcp
```

It prints two lines and copies them to the clipboard. Paste them once into MD's **Main
Menu → Plugins → Python Editor** and press **Run**:

```python
path = r"C:\Users\you\AppData\Local\md-mcp\md_mcp_listener.py"
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), {"__file__": path, "__name__": "__main__"})
```

That opens a control window and adds an **md-mcp** entry to MD's Plugin tab; later
sessions are one click there. **Press Start to open the port** — the window comes up idle
because `run_md_python` executes arbitrary Python inside MD, which should take a
deliberate press.

Run it with nothing else going: MD refuses a registration made from a script running
inside another one. **That Run never finishes, and should not** — the listener *is* the
running script, holding MD's main thread and pumping its message queue. MD stays usable.

`plugin/README.md` covers the package, registering the button by hand, and moving the
port. `python tools/build_plugin.py` builds it; a `v*` tag publishes it beside the wheel.

## Tools

| Tool | What it does |
|---|---|
| `md_status` | Is MD reachable, which build, and what is in the scene |
| `md_api` | Search everything this build exposes, with full signatures |
| `md_call` | Call any one of those functions |
| `run_md_python` | Run a script inside MD and get back what it printed. `timeout_seconds` overrides the 300s default |

```
md_api("Simulate")     -> the simulation calls this build has, with their signatures
md_call("utility_api.Simulate", [400])
run_md_python("import pattern_api; print(pattern_api.GetPatternCount())")
```

**Search before calling.** MD dispatches overloads on arity and argument type, and
`md_api` gives every signature a call has — including for the 77 that are overloaded. For
an `ApiTypes` option struct it also names the fields and defaults, which MD documents
nowhere else. Of the 688 members, 599 are callable straight from `md_call`; the 37 taking
an MD option object need `run_md_python` to build one.

MD's bindings carry signatures and almost no prose, so a signature gives shape and not
meaning. Where behaviour has been measured, `md_api` attaches a `note` saying what the
call actually does — that `Simulate` returns `True` for the argument that leaves the
garment flat, that writing colour side 1 has ended the MD process. Notes annotate what
the live build reported and never replace it; `src/md_mcp/notes.py` holds them and
`docs/md-conventions.md` has the long form.

## Reading MD back

Lengths are millimetres whatever the UI shows. There is no undo anywhere in the API, and
`utility_api.NewProject()` discards the scene without asking.

**MD fails quietly more often than it fails loudly**, so read the scene back rather than
trusting a return value — `md_status` and `run_md_python` both report the resulting
pieces, seam groups, avatars and fabrics for that reason. Three measured traps:

- `utility_api.Simulate(n)` runs *n* solver steps and returns `True` for any *n*.
  `Simulate(1)` leaves the garment flat and reports success; a few hundred is a drape.
- `AddSeamlinePairGroup` refuses a nonexistent index, but validates indices and not
  meaning: a seam joining a hem to a neckline is accepted and counted.
- `fabric_api.GetFabricCount()` answers `0` for a scene that has fabrics, and `AddFabric`
  wants a `.zfab` path — returning `0`, a plausible index, for anything else.

The tools fail loudly when MD is not listening, so "no patterns" can only mean an empty MD.

Two proven ways to add a sewing pattern: call by call
(`CreatePatternWithPoints` + `AddSeamlinePairGroup` + `Simulate`), or as a document via
`ExportPatternJSON` / `ImportPatternJSON`, which round-trips pieces, names, seams and
lengths and reaches what the call API cannot — notches, seam allowances, button
placement. `.zprj` and `.zpac` move whole projects; DXF is unreachable.

## How it works

MD cannot be dialled into — this build has no `REST_API` module — so MD hosts the socket.
`plugin/md_mcp_listener.py` runs inside MD, binds `127.0.0.1:5121` and serves one JSON
request per connection; `src/md_mcp/bridge.py` is the other end.

The serve loop runs on **MD's own main thread** because MD only releases the GIL while a
script is executing: a listener on a daemon thread binds its port and then freezes.
`docs/adr/001-bridge-transport.md` has the reasoning and what it beat.

`run` executes arbitrary Python inside MD over loopback with no authentication — the same
trust boundary as MD's own script menu.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest                  # 93 tests, no MD needed
.venv\Scripts\python.exe -m pytest -m md_required   # 42 more, MD open (most need it empty)
```

The first set runs the shipped listener — and the packaged copy — against fake MD
modules, proving the protocol and nothing about Marvelous Designer. The second proves MD:
avatar import, drafting, inner shapes, sewing, topstitch, fabric, per-piece physics,
simulation, OBJ export and both pattern routes, each measured rather than assumed. It
skips with instructions when MD is not listening, and refuses to run unless the scene is
empty, because it calls `NewProject`.
