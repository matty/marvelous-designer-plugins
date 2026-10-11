# md-mcp

An MCP server that gives a model the whole of a running Marvelous Designer's Python API —
688 calls across six modules, none of them wrapped, curated or modelled.

Windows only, since Marvelous Designer is. Tested against MD 2026.0.315.

## Setup

There are two halves: the MCP server your client runs, and a plugin inside Marvelous
Designer that it talks to. Both come from the
[releases page](https://github.com/matty/marvelous-designer-plugins/releases) and must
be the same version.

**1. The server.** With Python 3.11 or newer:

```powershell
python -m venv "$env:LOCALAPPDATA\md-mcp-server"
& "$env:LOCALAPPDATA\md-mcp-server\Scripts\pip" install .\md_mcp-<version>-py3-none-any.whl
```

Then register `%LOCALAPPDATA%\md-mcp-server\Scripts\md-mcp.exe` as a stdio MCP server
with your client. For Claude Code:

```powershell
claude mcp add marvelous-designer -- "$env:LOCALAPPDATA\md-mcp-server\Scripts\md-mcp.exe"
```

**2. The plugin.** Unzip `md-mcp-plugin-<version>.zip` and run `install.ps1` from it.
It copies the listener to `%LOCALAPPDATA%\md-mcp`, prints two lines and copies them to
the clipboard. Paste them once into MD's **Main Menu → Plugins → Python Editor** and
press **Run**:

```python
path = r"C:\Users\you\AppData\Local\md-mcp\md_mcp_listener.py"
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), {"__file__": path, "__name__": "__main__"})
```

That starts the bridge and adds **Start MCP** and **Stop MCP**
to MD's Plugin tab; later sessions are one click there. Nothing listens until Start is
clicked, because `run_md_python` executes arbitrary Python inside MD.

The Run finishes straight away — a Windows timer on MD's main thread does the serving
from then on — and the bridge stops with **Stop MCP** or when MD closes. If an entry
does not appear, MD says why in
`C:\Users\Public\Documents\MarvelousDesigner\New Assets\api_plugin_log.txt`.

`plugin/README.md` covers registering the entries by hand, moving the port, and
upgrading. After installing a new version, click **Stop MCP** then **Start MCP** so MD
loads it; `md_status` says which version answered.

**From source** instead: `pip install -e .` in this checkout, and `.\plugin\install.ps1`
for the plugin.

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
| `md_guide` | Short, measured facts about how MD behaves -- what its return values do not prove, how seams, placement and saving really work -- to read before changing anything |

A modal dialog in MD holds the thread that serves the bridge. md-mcp watches for one from outside MD: a request blocked by a dialog gives up in about two seconds and says so, and `run_md_python`, `md_call` and `md_status` report a dialog left showing after a reply. It can name the dialog but not read its text, so look at MD.

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

The listener is served by a Windows timer on **MD's own main thread**, because MD only
releases the GIL while a script is executing: a listener on a daemon thread binds its
port and then freezes. `plugin/README.md` has the detail.

`run` executes arbitrary Python inside MD over loopback with no authentication — the same
trust boundary as MD's own script menu.

## Before changing a garment

[docs/garment-preflight.md](docs/garment-preflight.md) lists the checks to run before
editing patterns or adding seams, and after each small batch: overlap on the 2D board,
duplicate or overlapping sewing, and intersections in 3D.
[docs/md-conventions.md](docs/md-conventions.md) is the long form of every measurement,
with the MD version each was taken on; its examples are not design rules.

A script that fails partway does not roll back the edits it already made. Its reply
keeps whatever it printed and whatever scene state could be read, and a failed read-back
is reported separately from a failed script.

## Development

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest                  # no MD needed
.venv\Scripts\python.exe -m pytest -m md_required   # MD open; most need an empty scene
python tools\build_plugin.py                        # dist/md-mcp-plugin-<version>.zip
```

The first set runs the shipped listener against fake MD modules, which proves the
protocol and nothing about Marvelous Designer. The second needs MD open with the bridge
started; it calls `NewProject`, so it refuses to run against a scene with anything in it.
A `v*` tag publishes the plugin zip and the wheel. [AGENTS.md](AGENTS.md) has the rest.
