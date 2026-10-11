# Changelog

Both halves of the bridge — the md-mcp server and the plugin inside Marvelous Designer —
carry the same version. Measured against MD 2026.0.315.

## 0.4.0

**Upgrading:** install the new plugin, then click **Stop MCP** and **Start MCP** in MD
(or restart MD) so it loads the new listener. `md_status` shows which listener answered.

### Added

- **Start MCP and Stop MCP** in MD's Plugin tab. The listener now runs from a Windows
  timer on MD's main thread and returns straight away, so MD stays usable and closing MD
  ends the process. A message box appears only when something needs attention.
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

- Plugin registration: `RegisterPythonScript` takes the path first. Earlier versions
  passed the title first, and MD refused the entry.
- Camera views, `SetArrangementPosition`, snapshot output and MD's unsaved-changes flag
  are described as measured, where earlier notes were wrong.

### Removed

- The listener's small Start/Stop window, replaced by the two Plugin tab entries. An
  entry for the 0.3.0 listener, if you had one, still works as Start and can be removed
  in Plug-in Manager.

## 0.3.0

First release: `md_api`, `md_call`, `run_md_python` and `md_status` over a loopback
socket hosted by MD, with measured notes on 75 calls.
