# Roadmap

md-mcp is a bridge to Marvelous Designer plus a few tools that make MD's behaviour
visible: a reliable connection, replies that never claim more than MD did, a way to see
what MD actually holds, and a way back when something goes wrong. **It does not design
garments** — pattern making is the model's job, and no part of this plan teaches it.
**Every addition has to fix a failure seen in a real session**, not one that might
happen.

The [2026-10-11 cape session](sessions/2026-10-11-vampire-cape.md) is the session this
plan answers. Its failures that md-mcp can fix: seams nobody planned and nobody could
read back, no undo, an error MD showed on screen while the reply said success, and no
cheap way to look at the result. The background is in the
[research report](<research/reports/Marvelous Designer MCP research.md>).

## Done (2026-10-11)

- **Timer listener.** Start MCP arms a Windows timer and returns; Stop MCP stops it;
  closing MD ends the process. Live: started by a click, 15.9 ms median round trip,
  requests served one at a time, MD exited normally when closed.
- **Measurements corrected:** camera views (8 is the back; `md_snapshot` takes 0–9),
  `SetArrangementPosition` (works once the piece is re-placed), `RegisterPythonScript`
  (path first; MD logs every refusal).

## Next

1. **Land what is there.** Commit the uncommitted work by theme. Run the live suite once
   against the timer listener, plus a first live run of `md_measure`, `md_snapshot`,
   `md_arrangement_points` and `md_fabric_presets`. Start MCP stops showing a box when
   it succeeds, so a session costs one click.
2. ~~**`md_checkpoint` / `md_restore`.**~~ Built and live-tested. Checkpoints are `.zpac`
   (the garment, not the avatar): measured 2026-10-11, `ExportZPrj` re-points MD's open
   project at the copy, so the user's next Save would go over it, while `ExportZPac`
   leaves the project alone. Saving any copy clears MD's unsaved flag, so the tool sets
   it again. Restore keeps the open avatar and compares the garment with the record.
3. ~~**`md_seams`.**~~ Built: decodes `ExportPatternJSON`'s seam list into piece names,
   spans, line numbers and lengths, warns on an edge sewn twice or to itself, and diffs
   against an earlier read. Tested against a real export; the live test is written and
   waits for the next MD session.
4. **Dialog warning.** After a mutating reply, md-mcp lists MD's windows; if a dialog is
   showing, the reply says so instead of success.
5. ~~**`md_snapshot` sheet.**~~ Built: `views=[2, 4, 8]` returns front, side and back in
   one call, captured in one run inside MD. The live test waits for the next MD session.
6. **A short guide to MD's behaviour** (`md_guide`, text only), because the server
   instructions are nearly at Claude Code's 2,048-character limit: check the scene, not
   return values; read seams back with `md_seams` after sewing; Layer Clone sews every
   edge and nothing removes a seam; `SetArrangementPosition` acts only once the piece is
   re-placed; a coarse particle distance on narrow pieces raises meshing errors. MD facts
   only, each already measured in [md-conventions](md-conventions.md). The server
   instructions point at it.

Then **use it in a real session** and write it up. Anything more only if that session
shows md-mcp itself is what fell short.

## Small open questions

Measure when MD is next open; none blocks the list above except where noted.

- Is the cape session's meshing error a separate window, and is its text readable?
  (shapes 4)
- What does a seam's `Direction` mean — two rectangles sewn each way.
- Why did MD refuse to register Stop MCP? Until then, closing MD or a `shutdown`
  request stops the bridge.

## Not doing

Anything that designs garments — pattern drafting, GarmentCode, seam-plan or placement
helpers that encode garment knowledge — plus an unattended test-session tool, materials
work, and PyPI publishing (the name `md-mcp` is taken there anyway). The research report
keeps the detail.
