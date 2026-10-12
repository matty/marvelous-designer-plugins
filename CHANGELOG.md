# Changelog

Both halves of the bridge — the md-mcp server and the plugin inside Marvelous Designer —
carry the same version. Measured against MD 2026.0.315.

## 0.4.0

**Upgrading:** install the new plugin, then click **Start/Stop MCP** twice in MD — stop,
then start — (or restart MD) so it loads the new listener. `md_status` shows which listener answered.

### Added

- **A Claude Desktop extension**, `md-mcp-<version>.mcpb`. It carries its own Python
  and a copy of the plugin, so nothing needs installing first. Its settings take the
  bridge port and the checkpoint folder.
- **`md_draft_piece`, `md_sew` and `md_edges`**: draft a piece from named edges and sew
  edges by name. The outline is checked before MD sees it, MD's corners and lengths are
  read back after, and the edge names are kept in the garment, so checkpoints carry them.
  A seam has to say which ends meet, is refused if the edge is already sewn or sewn to
  itself, and is checked against MD's seam ledger once made.
- **`md_body`, `md_place` and `md_fit`**: measure the avatar, place pieces on it to a
  height and clearance, and check the garment in 3D before and after a drape -- seams,
  cloth inside the body, and how waists and necklines sit. Built on MD's OBJ export,
  whose per-vertex texture coordinates turn out to be each vertex's pattern position.
  - `md_body` splits each girth into its front and back arcs, so fronts and backs can be
    drafted to the body rather than to quarters.
  - `md_place` keeps a piece clear of every piece it has already placed.
  - `md_fit` finds pieces passing through each other, measures a ring against the body
    in the ring's own plane (a cuff, a sloping neckline), warns when a ring is stretched
    or drafted smaller than the body it goes round, measures how far apart unsewn edges
    hang (`gaps`, for open fronts and vents), and names seams by their edges.
- **`md_snapshot(pattern=True)`**: a picture of the 2D pattern, every piece whole.
- **The lines to paste into MD**: when MD is not listening, `md_status` and every other
  tool give the exact two lines that start the plugin from MD's Python Editor.
- **Start/Stop MCP** in MD's Plugin tab: one click starts the bridge, the next stops it.
  The listener now runs from a Windows timer on MD's main thread and returns straight
  away, so MD stays usable and closing MD ends the process. A message box appears only
  when something needs attention.
- **`md_checkpoint` and `md_restore`**: save the garment to a `.zpac` and put it back,
  keeping the open avatar. MD's open project is left alone, and MD still asks to save
  before closing.
- **`md_seams`**: what MD has actually sewn to what — pieces, spans, line numbers and
  lengths — with warnings for an edge sewn twice or to itself, and a diff against an
  earlier read.
- **`md_measure`, `md_arrangement_points`, `md_snapshot` and `md_fabric_presets`** to
  read the 3D scene back. `md_snapshot(views=[2, 4, 8])` returns front, side and back in
  one call.
- **`md_guide`**: short, measured facts about how MD behaves, to read before changing
  anything.
- **Modal dialog detection.** A request blocked by a dialog in MD gives up after about
  two seconds and names it; `run_md_python`, `md_call` and `md_status` report a dialog
  left showing.
- **Checked exports.** Every `Export*` call is checked for the files it claims to have
  written; `missing_outputs` means MD reported a file that is not there.
- Tool annotations, so clients can tell read-only tools from ones that change the scene.

### Fixed

- `MD_MCP_PORT` set to something that is not a port is refused by name. An empty value
  means the default, 5121.
- Plugin registration: `RegisterPythonScript` takes the path first. Earlier versions
  passed the title first, and MD refused the entry.
- **A bridge MD could not stop.** MD refused the separate Stop MCP entry, so only Start
  appeared. The one entry now toggles, and an entry you added by hand for the listener
  (named `md_mcp_listener`) stops it too.
- Camera views, `SetArrangementPosition`, snapshot output and MD's unsaved-changes flag
  are described as measured, where earlier notes were wrong.

### Removed

- The listener's small Start/Stop window, replaced by the two Plugin tab entries. An
  entry for the 0.3.0 listener, if you had one, still works as Start and can be removed
  in Plug-in Manager.

## 0.3.0

First release: `md_api`, `md_call`, `run_md_python` and `md_status` over a loopback
socket hosted by MD, with measured notes on 75 calls.
