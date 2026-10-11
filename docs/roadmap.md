# Roadmap

md-mcp is a bridge to Marvelous Designer plus a few tools that make MD's behaviour
visible: a reliable connection, replies that never claim more than MD did, a way to see
what MD actually holds, and a way back when something goes wrong. **It does not design
garments** — pattern making is the model's job, and no part of this plan teaches it.
**Every addition has to fix a failure seen in a real session**, not one that might
happen.

The [2026-10-11 cape session](sessions/2026-10-11-vampire-cape.md) is the session this
plan answered. Its failures that md-mcp could fix: seams nobody planned and nobody could
read back, no undo, an error MD showed on screen while the reply said success, no cheap
way to look at the result, and MD still running after it was closed. The background is
in the [research report](<research/reports/Marvelous Designer MCP research.md>).

## Done (2026-10-11)

All live-tested against MD 2026.0.315: 52 live tests and the offline suite pass. The
check that every call `md_guide` names exists waits for the next MD session.

- **Timer listener.** Start MCP arms a Windows timer and returns; Stop MCP stops it;
  closing MD ends the process. 15.9 ms median round trip, one request at a time. Start
  shows a box only when something needs attention.
- **`md_checkpoint` / `md_restore`.** Checkpoints are `.zpac` (the garment, not the
  avatar), because every `ExportZPrj` re-points MD's open project at the copy. Saving any
  copy clears MD's unsaved flag, so both tools set it again.
- **`md_seams`.** What is sewn to what: pieces, spans, line numbers and lengths, warnings
  for an edge sewn twice or to itself, and a diff against an earlier read.
- **Dialog warning.** A blocked request gives up in about two seconds and names the
  dialog; `run_md_python`, `md_call` and `md_status` report one left showing. Its text is
  not readable from outside MD.
- **`md_snapshot` views.** `views=[2, 4, 8]` returns front, side and back in one call.
- **`md_guide`.** Measured facts about MD the server instructions have no room for; the
  instructions point at it first.
- **Measurements corrected:** camera views, `SetArrangementPosition`, `RegisterPythonScript`
  (path first; MD logs every refusal and keeps its list in `pluginSettings.json`),
  snapshot bytes, and the unsaved flag (renaming a piece does not set it).

## Next

**Use it in a real session** and write it up in `docs/sessions/`. Anything more only if
that session shows md-mcp itself is what fell short.

## Small open questions

Measure when MD is next open; none blocks anything.

- Is the cape session's meshing error the same kind of window as the seam dialog? The
  detector keys on any dialog MD's main window owns, so it should catch it; not yet seen.
- What does a seam's `Direction` mean? Two rectangles sewn each way would say.
- Why does MD claim `md_mcp_stop.py` is already registered? Until it is in the Plugin
  tab -- added by hand in Plug-in Manager, or by MD -- closing MD or a `shutdown` request
  stops the bridge.

## Not doing

Anything that designs garments — pattern drafting, GarmentCode, seam-plan or placement
helpers that encode garment knowledge — plus an unattended test-session tool, materials
work, and PyPI publishing (the name `md-mcp` is taken there anyway). The research report
keeps the detail.
