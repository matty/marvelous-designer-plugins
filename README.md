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

That starts the bridge and adds **Start MCP** and **Stop MCP**
to MD's Plugin tab; later sessions are one click there. Nothing listens until Start is
clicked, because `run_md_python` executes arbitrary Python inside MD.

Run it with nothing else going: MD refuses a registration made from a script running
inside another one. The Run finishes straight away — a Windows timer on MD's main
thread does the serving from then on — and the bridge stops with **Stop MCP** or when MD
closes.

`plugin/README.md` covers the package, registering the entries by hand, and moving the
port. `python tools/build_plugin.py` builds it; a `v*` tag publishes it beside the wheel.

## Tools

| Tool | What it does |
|---|---|
| `md_status` | Is MD reachable, which build, and what is in the scene |
| `md_api` | Search everything this build exposes, with full signatures |
| `md_call` | Call any one of those functions |
| `run_md_python` | Run a script inside MD and get back what it printed. `timeout_seconds` overrides the 300s default |
| `md_measure` | 3D bounds of the avatar and of each garment group, plus avatar slices at given heights, read from OBJ exports MD makes and deletes |
| `md_arrangement_points` | Where arrangement points really are in 3D. **Temporarily adds one pattern piece**, arranges it at each point, and deletes it; reports the before/after piece count |
| `md_snapshot` | Pictures of the 3D window, returned as images. `view` 0–9 moves the camera (2 front, 8 back, 4 and 6 the sides) and leaves it there; `views=[2, 4, 8]` gives front, side and back in one call; `max_size` caps each image's long edge, default 1024 px |
| `md_fabric_presets` | The `.zfab` files `AddFabric` needs, found beside the running MD and in Public and user Documents |
| `md_seams` | What MD has actually sewn to what: each seam's pieces, spans and line numbers, warnings for an edge sewn twice or to itself, and what changed since an earlier read |
| `md_checkpoint` | Save the garment to a checkpoint `.zpac`, with a record of what it held. Leaves MD's open project alone, which a `.zprj` save does not, and keeps MD asking to save before closing |
| `md_restore` | Put a checkpoint's garment back (`latest`, a label, or a path), keeping the open avatar, and say whether it matches the record |

```
md_api("Simulate")     -> the simulation calls this build has, with their signatures
md_call("utility_api.Simulate", [400])
run_md_python("import pattern_api; print(pattern_api.GetPatternCount())")
md_measure(heights=[900, 1200, 1400])          -> avatar bounds and three slices, in mm
md_arrangement_points(points=["Body_Back"])     -> every point with Body_Back in its name
md_snapshot(view=4, max_size=800)
md_fabric_presets("denim")
```

The call API has no 3D readback, so the last four read the scene the way that was
measured to work (2026.0.315): `ExportOBJ` with its option object, millimetres, Y up and
+Z the avatar's front; `SetCamViewPoint` then `ExportSnapshot3D`. They are scripts sent
through the same bridge and need nothing new installed in MD.

**Exports are checked, not believed.** `ExportOBJ` into a folder that did not exist
returned its path list and wrote nothing. While any script runs — `md_call`'s included —
the listener checks each `Export*` call's named and returned paths as it returns:
`writes` lists what it found, `missing_outputs` and a `warning` mean MD reported a file
that is not there, and a missing parent folder of an absolute path is created first and
named in `created_folders`. This part lives in the listener, so it needs the listener in
MD reloaded; an older one leaves an `md_call` export marked `write_check: unverified`.

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

## Garment construction context

Read [garment-preflight.md](docs/garment-preflight.md) before editing patterns or adding
seams. It separates board overlap, duplicate/overlapping sewing, and 3D intersections,
with checks before and after a small batch. [md-conventions.md](docs/md-conventions.md)
contains versioned measurements; examples are not universal design constraints.

Script replies now distinguish execution success from unavailable scene read-back,
retain output and available partial state on failure, and include a complete available
piece list on every run. Script exceptions do not roll back earlier edits.
