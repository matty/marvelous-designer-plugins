# Design lessons for md-mcp from MCP servers for adjacent creative/3D applications, and from general agent tool-design guidance

Researched 2026-10-11. Web sources only; no MCP tools for Blender, Substance, Unity or MD were called. "Session" below means the Claude Code session in which this research ran, where the Blender, Substance, Unity and MD servers were connected. md-mcp facts come from `AGENTS.md`, `docs/roadmap.md`, `docs/sessions/2026-10-11-vampire-cape.md` and `src/md_mcp/server.py` in this repo.

## Q1. Blender MCP: which repo, how it is built, and how it handles images, errors, threading, undo and guidance

### Takeaway
The tool set in this setup (`execute_blender_code`, `look`, `get_scene_info`, `get_addon_status`, `search_assets`, `import_asset`, `generate_3d`, `record_trajectory_feedback`, `disable_telemetry`) is ahujasid/blender-mcp, renamed "MCP for Blender" (PyPI `mcp-for-blender`) in September 2026. Its design is **raw code first, plus a few high-leverage structured tools**. The heaviest of these is `look`, which returns the picture inline as MCP image content. Domain gotchas were deliberately moved into server instructions. It has no checkpoint or undo tool of its own, only "save your work first".

### Cited Findings
**Identity and scale**
- The repo is github.com/ahujasid/blender-mcp: about 30.5k stars, MIT licence, a community project not affiliated with the Blender Foundation. It was formerly `blender-mcp`; the PyPI package is now `mcp-for-blender`, and `uvx blender-mcp` setups keep working — [README](https://github.com/ahujasid/blender-mcp)
- The rename is announced in issue #366, "blender-mcp is now mcp-for-blender", opened 2026-09-16 — [issues](https://github.com/ahujasid/blender-mcp/issues)
- The README lists nine model-facing tools: `execute_blender_code`, `look`, `get_scene_info`, `generate_3d` (Tripo, Hunyuan3D, Hyper3D Rodin), `search_assets`/`import_asset` (Poly Haven, Sketchfab, Poly Pizza), `get_addon_status`, `disable_telemetry` and `record_trajectory_feedback` — [README](https://github.com/ahujasid/blender-mcp)

**Architecture and threading**
- A Blender add-on (`addon.py`) runs a socket server inside Blender. The MCP server (`src/blender_mcp/server.py`) connects to it on `localhost:9876` (override with `BLENDER_HOST`/`BLENDER_PORT` or `--port`) using JSON over TCP: `{type, params}` in, `{status, result|message}` out. There is no authentication or encryption, so anyone who can reach the port can run Python in Blender — [README](https://github.com/ahujasid/blender-mcp)
- Threading: the listener and each client run on daemon threads, which never touch `bpy`. They push `(command, client)` onto a queue, which a `bpy.app.timers` callback drains on Blender's main thread every 0.05 s. The timer is registered once from the main thread, and the code warns: "Never call bpy.app.timers.register() from here - it is not thread-safe." — [addon.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/addon.py)
- The MCP side holds a lock across each send and receive, so replies cannot interleave. It uses a 180 s timeout, clears the socket on timeout or error, and reconnects lazily on the next command — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)
- Framing is unresolved. The portion of `addon.py` read (the first 100k of 270k characters) accumulates raw JSON until `json.loads` succeeds — [addon.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/addon.py). Issue #311 (2026-08-09) says PR #292 moved `main` to 4-byte length-prefixed frames while the PyPI 1.8.0 client still sent raw JSON — [issue #311](https://github.com/ahujasid/blender-mcp/issues/311). The current add-on may accept both; I did not confirm this.

**Errors from inside the host**
- `execute_code` runs `exec` in a fresh namespace containing only `bpy`, with stdout captured to a `StringIO`. Only printed output is returned. On an exception it returns JSON carrying `exception_type`, `message` and the full `traceback.format_exc()` — [addon.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/addon.py)
- The server turns that into "Error executing code: " plus type, message and traceback — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)
- An optional safe mode (`BLENDER_MCP_SAFE_MODE=1`, off by default) checks scripts before they run and blocks file access, subprocesses, network access and code that persists. "Blocked scripts return the reason to the AI so it can retry" — [README](https://github.com/ahujasid/blender-mcp). The check lives in the MCP server ("Rejected by safe mode") — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)

**Images: `look`**
- `look` returns a `CallToolResult` with `ImageContent` (base64 PNG, `image/png`). `max_size` defaults to 768 and is clamped to 200–2000 — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)
- Modes:
  - `viewport` (the default) and `camera`.
  - `angles`: one tiled sheet of named views (`front`, `back`, `left`, `right`, `top`, `three_quarter`) or an `[x, y, z]` direction.
  - `frames`: an animation strip of 2–12 frames.
  - Shading can be `solid`, `material`, `rendered`, `wireframe` or `xray`.

  Retired modes return an error naming their replacement. A fallback to the native screenshot adds a text note saying so. The docstring says every setting changed to take the picture is restored afterwards — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)
- Capture draws the 3D view into a `gpu.types.GPUOffScreen` scaled to the longest side, falling back to `bpy.ops.screen.screenshot_area`. It reports which method was used, the image size and the view matrices — [addon.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/addon.py)
- The server instructions say: "look is how you see your work; use it as much as you need. Images stay in the conversation, so a smaller max_size keeps long sessions cheap." — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)

**Structured read-back**
- `get_scene_info` takes:
  - `query`, a name filter;
  - `root`, a subtree;
  - `fields`, column groups: placement, contents, health (topology, weights) and settings;
  - `limit`, default 20 lines.

  Truncated output tells the model to narrow with `query`/`root` or raise `limit`, and unknown field names are errors — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)

**Annotations**
- Only `readOnlyHint=True` is used, and only on a few viewport tools. Some tools carry UI metadata marking them app-only. There is no `destructiveHint`, `idempotentHint` or `openWorldHint` — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)

**Undo and checkpoints**
- The server has no checkpoint, undo or save logic. `record_trajectory_feedback` accepts `accept`/`reject`/`undo`/`correction` but only records them; it performs no undo — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)
- The add-on listens to `undo_post`/`redo_post` to record events but pushes no undo steps — [addon.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/addon.py)
- The README's only safeguard: "ALWAYS save your work before using it" — [README](https://github.com/ahujasid/blender-mcp)

**Guidance delivery**
- The server passes `SERVER_INSTRUCTIONS` to `FastMCP(instructions=...)`. They open with "MCP for Blender drives the user's live Blender." and cover:
  - start with `get_addon_status`/`get_scene_info`;
  - look shader nodes up by type, not by localised name;
  - read enum identifiers rather than hardcoding them;
  - put material colour on shader inputs;
  - size imported models from the reported bounding box.

  Source: [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)
- PR #348 ("Deliver code-correctness guidance via server instructions", merged 2026-09-15) moved those rules out of the `asset_creation_strategy` MCP prompt into server instructions.
  - Why: clients only saw the prompt if they called `prompts/get`, and `initialize` had been returning `instructions: None`. The PR argues these errors are deterministic, so retries cannot fix them.
  - Size: about 1.5 KB, with new tests checking that the instructions are advertised, name the relevant APIs and stay within a size budget.
  - Effect: no measured improvement in code quality is reported.

  Source: [PR #348](https://github.com/ahujasid/blender-mcp/pull/348)

**Context cost**
- Issue #347, measured by the mcp-context-cost tool with the o200k tokeniser: the tool-schema cost rose from 5,462 to 6,928 tokens (+27%) between 2026-08-19 and 2026-09-03, while the tool count went from 25 to 28. A fork later committed "Cut the MCP tool catalog's context cost by half" (2026-09-25) — [issue #347](https://github.com/ahujasid/blender-mcp/issues/347)

**Known issues and limitations**
- From the README — [README](https://github.com/ahujasid/blender-mcp):
  - Poly Haven downloads run on Blender's main thread and freeze the UI.
  - Complex requests may need splitting, and can time out.
  - Run only one MCP client instance at a time.
  - Python environment conflicts can break installs.
- From the open issues — [issues](https://github.com/ahujasid/blender-mcp/issues):
  - #311: protocol skew, where users saw "invalid frame length" and `WinError 10054`. This "looked like a broken install rather than a version mismatch" — [issue #311](https://github.com/ahujasid/blender-mcp/issues/311)
  - #376: add-on hints point at a "Start MCP Server" button that no longer exists.
  - #372: `get_viewport_screenshot` fails on Windows with a Docker setup.
  - #360: users asking what telemetry sends.
- Telemetry: by default a minimal anonymous usage record is collected; content collection (prompts, code, screenshots, trajectory steps) is opt-in — [README](https://github.com/ahujasid/blender-mcp)

### Inferences
- The README's nine tools against the 25–28 measured in early September suggest the model-facing catalogue was consolidated in late September or October 2026. `look` absorbing the retired modes (`topology`, `rig`, `image`) fits that. I could not date the consolidation.
- Blender's main-thread model (background socket threads, a queue, a main-thread timer) relies on a host timer API. MD has none, which is why md-mcp's serve loop has to pump MD's message queue itself (AGENTS.md). The shared invariant is that host API calls happen only on the host's main thread, one at a time, behind a lock.
- `look` sets the standard for md-mcp's verification tooling:
  - one call gives a picture, small by default;
  - several angles come back as one tiled image;
  - shading modes such as wireframe and x-ray show structure;
  - the view state is restored afterwards.

  MD cannot read its camera back (md-mcp's `md_snapshot` docstring), so the last point cannot be copied.
- Issue #311 is the same class of failure as md-mcp's "MD keeps a registered plugin's path for good" warning: an old half answers confidently. md-mcp's `ping` carrying version, commit and path is already better than Blender's situation, which has no handshake.

### Gaps
- I did not read the last 170k characters of `addon.py`, so the current framing and the `look` render path inside the add-on are unconfirmed.
- No published measurement exists of how much `look` improves outcomes, and I found no changelog dating its introduction.

## Q2. Substance 3D Designer MCP: architecture, validate-then-apply patches, capabilities, errors, threading, undo

### Takeaway
The server in this setup is MikeLi-28/Substance-3D-Designer-MCP: 29 allow-listed tools, **no Python or shell execution**, a versioned additive "Graph Patch 1.0" with a dry-run validator, an apply step that rolls back on failure, and runtime capability detection that separates "available" from "verified". It is the clearest model in the field for validate-then-apply and for honest capability reporting. It is very new and small (5 stars), and its authoring tools are not yet verified on real hardware.

### Cited Findings
**Identity and status**
- The repo is github.com/MikeLi-28/Substance-3D-Designer-MCP, v1.1.0, with 5 stars — [GitHub](https://github.com/MikeLi-28/Substance-3D-Designer-MCP)
- Substance 3D Designer 16.0.3 is the supported baseline, and the 1.0.0 capability set is verified on real hardware there. The 1.1.0 authoring extensions are not yet verified on real hardware — [GitHub](https://github.com/MikeLi-28/Substance-3D-Designer-MCP)

**Architecture**
- The chain is: MCP host → stdio → an external Python 3.10+ MCP server → authenticated, length-prefixed JSON on 127.0.0.1 → a Designer Python plugin. The plugin has an allow-listed command registry and a Qt main-thread dispatcher. The external process never imports Adobe's `sd` module — [GitHub](https://github.com/MikeLi-28/Substance-3D-Designer-MCP)
- Wire protocol — [protocol.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/protocol.md):
  - Messages are a 4-byte big-endian length prefix followed by UTF-8 JSON, up to 4 MiB.
  - Timeouts: read 5 s, write 30 s. There is one request per connection.
  - Each request carries a 256-bit session token, compared with `hmac.compare_digest` before any command lookup.
  - The plugin writes `~/.substance-designer-mcp/session.json`, holding host, ephemeral port, token, PID, Designer and plugin versions, and UTC start time.

**Threading**
- Each MCP call becomes one bridge request with one UUID, and the plugin posts one job to the Qt main thread. "A timeout cancels a queued job if it has not started"; a job already running in Adobe's code is allowed to finish — [architecture.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/architecture.md)
- Writes are serialised — [security-model.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/security-model.md)

**Tools (29)**
- Read: `sd_ping`, `sd_get_application_info`, `sd_get_capabilities`, `sd_list_packages`, `sd_get_active_graph`, `sd_list_graph_nodes` (paginated), `sd_get_selection`, `sd_get_node`, `sd_list_node_properties`, `sd_search_library`, `sd_list_node_definitions`, `sd_get_graph_snapshot`, `sd_validate_graph_patch` — [GitHub](https://github.com/MikeLi-28/Substance-3D-Designer-MCP)
- Write: `sd_create_node`, `sd_create_instance_node`, `sd_move_nodes`, `sd_connect_nodes`, `sd_disconnect_nodes`, `sd_set_node_parameter`, `sd_create_package`, `sd_create_graph`, `sd_open_graph`, `sd_create_graph_output`, `sd_apply_graph_patch`, `sd_import_bitmap` — [GitHub](https://github.com/MikeLi-28/Substance-3D-Designer-MCP)
- Destructive, requiring `confirm: true`: `sd_delete_nodes`, `sd_save_package`, `sd_save_package_as`, `sd_export_package_sbsar` — [GitHub](https://github.com/MikeLi-28/Substance-3D-Designer-MCP)

**Validate-then-apply**
- `sd_validate_graph_patch` is a dry run. It checks definitions, ports, types, targets and cycles — [GitHub](https://github.com/MikeLi-28/Substance-3D-Designer-MCP). It is annotated read-only and idempotent — [Glama tool page](https://glama.ai/mcp/servers/MikeLi-28/Substance-3D-Designer-MCP/tools/sd_validate_graph_patch)
- `sd_apply_graph_patch` is additive only and rolls back every created node on failure — [GitHub](https://github.com/MikeLi-28/Substance-3D-Designer-MCP); [Glama tool page](https://glama.ai/mcp/servers/MikeLi-28/Substance-3D-Designer-MCP/tools/sd_apply_graph_patch)
- Graph Patch 1.0 is a bounded, additive action schema — [architecture.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/architecture.md):
  - the plugin resolves every definition, resource, parameter and port before mutating anything;
  - it rejects duplicate target inputs and cycles;
  - it uses request-local aliases;
  - it never edits or deletes nodes that existed before the request;
  - if a runtime step fails, the plugin deletes every node that request created.

**Capabilities**
- Capability detection separates availability from verification. 16.0.3 is `verified`/`supported`; other 16.x releases are `untested`/`capability_detected`; newer majors are `untested`/`experimental`; older or unidentifiable versions are `untested`/`unsupported` — [architecture.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/architecture.md)
- "Runtime API availability controls individual tools" — [architecture.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/architecture.md)
- "Capability detection does not replace real-version testing." — [GitHub](https://github.com/MikeLi-28/Substance-3D-Designer-MCP)

**Errors**
- Errors carry a stable `code`, a safe `message` and JSON `details`; tracebacks stay in local logs — [protocol.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/protocol.md)
- Internal exceptions go to a rotating local log and surface as `INTERNAL_ERROR` or a narrower safe code — [security-model.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/security-model.md)

**Security posture and undo**
- The bridge is "a local privilege boundary, not a general automation endpoint", with "no Python, code, script, shell, terminal, or system command execution". It gives no explicit rationale beyond the allow-list design — [security-model.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/security-model.md)
- Save-as paths must be absolute, carry the right extension and have an existing parent directory. Overwriting needs an explicit opt-in — [security-model.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/security-model.md)
- There is no user-facing undo; the only rollback is the patch rollback — [security-model.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/security-model.md)

**Guidance**
- The README contains no server instructions text — [README](https://raw.githubusercontent.com/MikeLi-28/Substance-3D-Designer-MCP/main/README.md)
- In this session the server's instructions are one line: "Operate Adobe Substance 3D Designer through structured references and explicit runtime validation. Never guess node definitions, properties, or resource URLs." (observed in-session; no public URL found).

### Inferences
- The patch design maps closely onto md-mcp's roadmap P2.1 (seam plan: validate, then apply). The parts worth copying:
  - a versioned plan schema;
  - resolve every reference (piece, line, interval) before mutating;
  - request-local aliases, so a plan can name pieces it creates itself;
  - additive only;
  - a separate dry-run tool annotated `readOnlyHint`/`idempotentHint`;
  - roll back on failure.
- Substance's rollback deletes only the nodes the request created. md-mcp cannot do the equivalent for seams, because no MD call removes a seam (session report). An apply in MD therefore needs checkpoint-then-restore (roadmap P0.1) as its rollback, not deletion.
- "Available versus verified" is a ready-made model for md-mcp's `notes.py`. Each measured note already carries the build it was measured on, so `md_status` could report the build as verified (2026.0.315) or merely capability-detected.
- The cancel-if-not-started timeout matches md-mcp's modal-dialog failure mode. Once MD's main thread is busy, nothing can pre-empt it, and the honest reply is "queued but not started" or "started and still running, look at MD".

### Gaps
- I did not find the actual Graph Patch 1.0 field schema (`docs/schemas/` was not read), the full list of error codes, or measured success rates for the patch path against per-node tools.
- I found no user-reported issues: the repo is too new and small.

## Q3. Unity MCP: CoplayDev "MCP for Unity" and Unity's own official MCP

### Takeaway
CoplayDev's server is **structured first**:
- 50 `manage_*`-style tools sorted into 10 groups, with only `core` (30 tools) visible by default;
- raw C# (`execute_code`) is an opt-in group;
- `batch_execute` for many edits in one round trip;
- an editor-state resource with a `ready_for_tools` signal;
- `read_console` as the error channel after script changes;
- `manage_camera` screenshots returned inline;
- editor undo/redo exposed as actions;
- `unity_reflect`/`unity_docs` so the model verifies APIs against the live build instead of training data.

Its open issues are dominated by domain reloads, blocking editor popups and log noise: the same family as MD's modal dialogs. Unity's official MCP (in beta) is a relay to an editor bridge, with per-client approval and attribute-registered tools.

### Cited Findings
**Identity, architecture and transport**
- The repo is github.com/CoplayDev/unity-mcp: 14.8k stars, MIT licence, latest release v10.3.0 on 2026-10-04 (v10.2.0 on 2026-09-01). It has a Unity package (`MCPForUnity`) plus a Python server, supports Unity 2021.3 to 6.x, and is sponsored by Aura — [GitHub](https://github.com/CoplayDev/unity-mcp)
- The Unity plugin "Receives commands on the Unity main thread". HTTP suits multi-agent or remote setups, and stdio suits a single client — [docs](https://coplaydev.github.io/unity-mcp/)
- The transport defaults to stdio; HTTP defaults to `http://127.0.0.1:8080` — [main.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/main.py)

**Tool count and groups**
- 50 tools in 10 groups — [tool reference](https://coplaydev.github.io/unity-mcp/reference/tools):
  - `core` (30 tools, "always on by default") includes `batch_execute`, `manage_scene`, `manage_gameobject`, `manage_components`, `manage_asset`, `manage_material`, `manage_camera`, `manage_editor`, `read_console`, `refresh_unity`, `validate_script`, `script_apply_edits`, `get_sha`, `find_gameobjects`, `execute_menu_item` and `manage_tools`.
  - Optional groups: `animation`, `asset_gen` (which even includes a `blender_bridge`), `docs` (`unity_docs`, `unity_reflect`), `probuilder`, `profiling`, `scripting_ext` (`execute_code`, "runs arbitrary C# in the editor"), `testing` (async `run_tests` returning a job_id plus `get_test_job` polling), `ui` and `vfx`.
- Groups are switched with `manage_tools(action="activate"|"deactivate"|"list_groups"|"sync"|"reset")`, per session. The stated reasons — [tool groups](https://coplaydev.github.io/unity-mcp/guides/tool-groups):
  - prompt size: "every visible tool adds tokens";
  - routing accuracy: fewer wrong-tool picks;
  - package dependencies: hidden tools cannot raise confusing errors.

**Batching**
- `batch_execute`'s description calls it "STRONGLY RECOMMENDED" and claims 10–100x lower latency and token cost — [batch_execute.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/services/tools/batch_execute.py):
  - parameters are `commands` (each `{tool, params}`), `parallel` (read-only commands only), `fail_fast` and `max_parallelism`;
  - the default limit is 25 commands, configurable in Unity up to 100;
  - it is annotated `destructiveHint=True`;
  - the source does not show whether a batch is atomic.

**Images**
- `manage_camera` actions `screenshot`/`screenshot_multiview` — [manage_camera.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/services/tools/manage_camera.py):
  - `include_image` returns "inline base64 PNG", default false;
  - `max_resolution` defaults to 640 on the longest edge;
  - `batch` is `surround` (6 angles) or `orbit` (default 8, up to 36);
  - `screenshot_multiview` is shorthand for surround with `include_image=true`;
  - images are returned as MCP `ImageContent`.

**Undo**
- `manage_editor` has `undo`/`redo` actions that "perform Unity editor undo/redo and return the affected group name" — [manage_editor.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/services/tools/manage_editor.py)

**Guidance: instructions plus a skill**
- `_build_instructions()` builds the server instructions as a fixed text. Only the custom-tools paragraph varies, and nothing depends on which groups are enabled — [main.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/main.py)
- The server code does not handle domain reloads. Recovery is left to the agent, which the instructions tell to poll `mcpforunity://editor/state` for `data.compilation.is_compiling` — [main.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/main.py)
- The instructions tell the model to use resources to read editor state and tools to mutate it, and to call `read_console` after script changes. They also warn that "LLM training data frequently contains incorrect, outdated, or hallucinated Unity APIs" and tell the model to verify with `unity_reflect`/`unity_docs` (text built in [main.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/main.py); also observed in-session).
- The repo also ships a skill, `unity-mcp-skill/references/workflows.md` — [workflows.md](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/unity-mcp-skill/references/workflows.md). Its rules:
  - read `editor/state` and wait for `ready_for_tools`, checking `blocking_reasons`;
  - `read_console` after script changes;
  - screenshot with `manage_camera` after visual changes, at a lower resolution for quick checks;
  - on a stale-file error, re-fetch the file's SHA and retry;
  - retry with backoff after a domain reload;
  - re-query ProBuilder face indices after every edit, because they change.

**Preconditions and validation for code edits**
- `get_sha` returns a script's hash without its contents, `validate_script` returns diagnostics, and `script_apply_edits` (structured method- and class-level edits) is recommended over raw text edits — [tool reference](https://coplaydev.github.io/unity-mcp/reference/tools)

**Known issues (open, 2026)** — all from the [issues page](https://github.com/CoplayDev/unity-mcp/issues)
- #1411: the agent is "often blocked by a 'Reload scene' popup".
- #1434: "Transport log lines pollute read_console error/warning results".
- #1394: `run_tests` reports false failures or hangs when a domain reload lands mid-job.
- #1265: the HTTP bridge session drops after domain-reload churn.
- #1454: EditMode `run_tests` auto-fails at 15 s when the Editor is unfocused.
- #1407: the Windows focus nudge loops.
- #1438: a request for a path allowlist on destructive file operations in `execute_code`.

**Unity's official MCP**
- Unity MCP 2.0.0-pre.1, in the AI Assistant package. The AI client speaks MCP over stdio to a relay binary (`~/.unity/relay/`), which reaches the Editor bridge over named pipes (Windows) or Unix sockets — [Unity docs](https://docs.unity3d.com/Packages/com.unity.ai.assistant@2.0/manual/unity-mcp-overview.html):
  - the bridge auto-starts with the Editor;
  - tools are registered at startup through `McpToolRegistry` and can be toggled in Project Settings;
  - direct external connections need user approval, and approved clients are remembered;
  - custom tools register through attributes, interfaces or runtime APIs.
- The open beta was announced 2026-05-11 for Unity 6. It needs a Unity Cloud project and a subscription — [Unity blog](https://unity.com/blog/unity-ai-mcp-how-to-get-started) (date taken from a search-result summary; not re-verified on the page).

### Inferences
- CoplayDev's pattern is "structured core, raw code opt-in, verification built in":
  - screenshots and the console are first-class;
  - readiness is exposed as data;
  - the APIs themselves can be reflected.

  md-mcp already has the equivalent of `unity_reflect` in `md_api`, which searches the live build. Unity's instructions push the model to use it before writing code, which md-mcp's instructions also do ("Search before calling").
- Unity's `get_sha` and stale-file retry are optimistic concurrency. The MD analogue: a seam plan or point edit carries a fingerprint of the scene it was planned against (piece count, piece names, seam group count), and apply refuses if the scene has changed. That matters because deleting a piece renumbers everything (session report).
- Issues #1411 (popup blocks the agent) and #1434 (noise in the error channel) are warnings for md-mcp's roadmap P0.2. When md-mcp captures MD's own messages, it must filter its own transport noise. A blocking modal should be reported as "MD is showing a dialog", not as a timeout.
- Unity's async `run_tests` plus `get_test_job` polling is the pattern for long MD simulations: start a job, poll it, never block the reply past the client timeout.

### Gaps
- `batch_execute` atomicity is unknown (the C# side was not read).
- Unity's official MCP tool list and screenshot support are not documented publicly; the docs say the list is visible only in the Editor.
- I found no measured success rates for either Unity server.

## Q4. Houdini, Maya, Unreal, Godot and Cinema 4D: what they do differently

### Takeaway
Two variations matter for md-mcp:
1. Several servers **use the host's own remote-execution channel** instead of a custom plugin: Houdini's hrpyc, Maya's commandPort, Unreal's Python Remote Execution. MD has no such channel (AGENTS.md: no `REST_API` module).
2. **Epic's official Unreal MCP hides its toolsets behind three meta-tools** (`list_toolsets`, `describe_toolset`, `call_tool`) and runs calls one at a time on the game thread. That is architecturally the same idea as md-mcp's `md_api`/`md_call`.

Houdini adds cook-error discovery and quad-view renders. Godot servers mostly drive Godot from outside, through its CLI and debugger.

### Cited Findings
**Unreal (Epic, official)**
- The Unreal MCP plugin in UE 5.8 is marked Experimental — [Epic docs](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor):
  - it speaks HTTP/SSE at `http://127.0.0.1:8000/mcp`, with no stdio;
  - "Tool calls run on the game thread, one at a time", and clients should not overlap calls;
  - by default `tools/list` returns three discovery meta-tools, `list_toolsets`, `describe_toolset` and `call_tool`, over toolsets such as `SceneTools`, `ActorTools`, `MaterialInstanceTools` and `ObjectTools`;
  - no shipping toolset advertises Resources or Prompts;
  - toolsets are authored in Python (`unreal.ToolsetDefinition`, `@toolset_registry.tool_call`) or in C++ (`UFUNCTION(meta=(AICallable))`);
  - there is no authentication, only loopback;
  - agent guidance: "keep tools small and focused, and prefer structured return types over free-form strings".

**Unreal (community)**
- `uemcp` uses Unreal's Python Remote Execution with no plugin to compile. It offers 29 dedicated tools plus a raw Python escape hatch, returns structured JSON with Python tracebacks, and reconnects after editor restarts — [PyPI uemcp](https://pypi.org/project/uemcp/)
- runreal/unreal-mcp uses the built-in Python remote execution protocol (UE 5.4+) and warns that agents get full editor access — [README via Glama](https://glama.ai/mcp/servers/@runreal/unreal-mcp/blob/90fb0177b34b428f6e803567a4bd746974478239/README.md)

**Houdini**
- oculairmedia/houdini-mcp connects through Houdini's built-in RPyC server (`hrpyc.start_server(port=18811)`), or runs as an in-Houdini plugin over stdio — [GitHub](https://github.com/oculairmedia/houdini-mcp):
  - 43 tools in 15 categories, including `execute_code` (full `hou` access) and `execute_hscript`;
  - `render_viewport`, `render_quad_view` (Front/Left/Top/Perspective), `capture_pane_screenshot` and `render_node_network`;
  - `find_error_nodes`, and `get_node_info(include_errors=True, force_cook=True)` for cook state, errors and warnings, with advice to check cook state before reading geometry;
  - auto-reconnect with exponential backoff, a consistent error format with recovery hints, and large responses truncated or summarised.
- A fork was reported working on Windows with Houdini 21.0 and RPyC 5.3.1 — [SideFX forum](https://www.sidefx.com/forum/post/461487/)

**Maya**
- No official Autodesk MCP server was found. The community servers use Maya's commandPort:
  - GG_MayaMCP supports Maya 2024+ and is unaffiliated with Autodesk — [mcpservers.org](https://mcpservers.org/servers/gimbalgoats/gg_mayamcp)
  - PatrickPalmer/MayaMCP uses the default command port with no plugin and describes itself as "early days … minimal set of functionality" — [mcpservers.org](https://mcpservers.org/servers/PatrickPalmer/MayaMCP)

**Godot**
- Coding-Solo/godot-mcp (6.0k stars) launches Godot processes rather than running inside the editor. It uses the CLI plus one bundled `godot_operations.gd` that receives JSON operations. Its tools include `launch_editor`, `run_project`, `get_debug_output`, `create_scene`, `add_node` and `save_scene`; it has no screenshots — [GitHub](https://github.com/Coding-Solo/godot-mcp)
- Other Godot servers:
  - LeeSinLiang/godot-mcp adds remote-debugger output (ports 6006/6007) and an experimental `capture_screenshot` — [README via Glama](https://glama.ai/mcp/servers/@LeeSinLiang/godot-mcp/blob/8731290490fd120acfe86a0ff35f1ac5a097588e/README.md)
  - minimal-godot-mcp splits console output into print, stdout and stderr, and adds GDScript diagnostics through Godot's Language Server — [Glama](https://glama.ai/mcp/servers/ryanmazzolini/minimal-godot-mcp)

**Cinema 4D**
- ttiimmaacc/cinema4d-mcp is a socket-server plugin started from Extensions > Socket Server Plugin > Start Server. It offers `execute_python` plus primitives, MoGraph, cameras, Redshift inspection and rendering — [README](https://cdn.jsdelivr.net/gh/ttiimmaacc/cinema4d-mcp@main/README.md)
- kumoproductions/mcp-cinema4d pairs an npm stdio server with a Python bridge on 127.0.0.1:18710 and has a `ping` tool. Plugin and package versions must match, and a mismatch shows only as "unknown command: <tool>" in the bridge log — [socket.dev](https://socket.dev/npm/package/@kumoproductions/mcp-cinema4d); [GitHub](https://github.com/kumoproductions/mcp-cinema4d)

### Inferences
- Epic's choice of three meta-tools over toolsets is independent support for md-mcp's search-then-call design (`md_api` + `md_call`). It suggests garment-level tools could also sit behind discovery, for example as a "garment" toolset described on demand, rather than each adding a tool definition to every session.
- Every mature server that keeps a raw escape hatch also adds structured read-back and error discovery: Houdini (`find_error_nodes`, quad view), uemcp (tracebacks) and Blender (`look`). Raw execution alone is nowhere treated as enough.
- The C4D version-mismatch symptom confirms again that a plugin/server handshake carrying versions is necessary. md-mcp already has one.
- Epic's "one call at a time on the game thread, don't overlap" is the same contract md-mcp's listener has with MD's main thread. Saying so in tool docs or instructions helps clients that would otherwise fire calls in parallel.

### Gaps
- Coverage is brief by design. Star counts and release dates for the Houdini, Maya and C4D servers were not checked.
- I found no screenshot or undo details for Epic's plugin, and no official SideFX or Maxon MCP.

## Q5. General guidance on designing tools for agents, and the MCP features that matter here

### Takeaway
The guidance pulls two ways:
- **Curated, consolidated, poka-yoke tools** with concise, high-signal replies and actionable errors (Anthropic, "Writing effective tools" and "Building effective agents"; SWE-agent's ACI).
- **Code execution** as the scalable way to compose many operations without flooding context (Anthropic, "Code execution with MCP"; Cloudflare's "Code Mode").

The MCP specification gives md-mcp specific tools for this: inline image content, `structuredContent`/`outputSchema`, `isError` for self-correctable execution errors (input validation included since 2025-11-25), behaviour annotations with conservative defaults, server instructions and experimental tasks for long jobs. Claude Code defers MCP tool definitions by default and truncates each tool description and each server's instructions at 2,048 characters.

### Cited Findings
**Anthropic, "Writing effective tools for agents" (Ken Aizawa, 2025-09-11)** — [Anthropic](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Don't simply wrap endpoints: "More tools don't always lead to better outcomes."
- Consolidate chained steps into one tool: "Tools can consolidate functionality, handling potentially *multiple* discrete operations (or API calls) under the hood."
- Prefer targeted search to listing, and namespace related tools.
- Return high-signal context and resolve opaque IDs to names. Offer a `response_format` of concise or detailed.
- Use pagination, filtering and truncation with sensible defaults. Claude Code caps tool responses at 25,000 tokens by default.
- "prompt-engineer your error responses to clearly communicate specific and actionable improvements".
- Describe tools as you would to a new hire.
- Evaluate on realistic multi-call tasks with held-out sets, and read transcripts.

**Anthropic, "Building effective agents" (2024-12-19)** — [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)
- "Poka-yoke your tools. Change the arguments so that it is harder to make mistakes."
- "we actually spent more time optimizing our tools than the overall prompt".
- After requiring absolute file paths, "the model used this method flawlessly".
- Agents need "ground truth" from the environment at each step.

**Anthropic, "Code execution with MCP" (Adam Jones and Conor Kelly, 2025-11-04)** — [Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp)
- Tool definitions and intermediate results passed through the model are costly.
- Exposing servers as code APIs cut one example "from 150,000 tokens to 2,000 tokens".
- Suggests a `search_tools` tool with detail levels: name only, name and description, or full schema.
- Code gives loops, conditionals and persisted state, and saved code can become skills.
- Requires "a secure execution environment".

**Anthropic, "Introducing advanced tool use" (Bin Wu et al., 2025-11-24)** — [Anthropic](https://www.anthropic.com/engineering/advanced-tool-use)
- Tool Search with `defer_loading` cut tool-definition overhead by about 85%. On MCP evaluations it raised Opus 4 from 49% to 74% and Opus 4.5 from 79.5% to 88.1%. It is most useful with more than 10 tools or more than 10K tokens of definitions.
- Programmatic Tool Calling cut tokens by 37% on complex research tasks.
- Tool Use Examples raised accuracy on complex parameter handling from 72% to 90%; the post recommends 1–5 examples per tool.

**Cloudflare, "Code Mode" (Kenton Varda and Sunil Pai, 2025-09-26)** — [Cloudflare](https://blog.cloudflare.com/code-mode/)
- "LLMs are better at writing code to call MCP, than at calling MCP directly", because "LLMs have seen a lot of code. They have not seen a lot of 'tool calls'".
- The post reports no quantitative benchmarks.

**RAG-MCP (Gan and Sun, 2025-05-06)** — [arXiv 2505.03275](https://arxiv.org/abs/2505.03275)
- Retrieving a subset of tools "more than triples tool selection accuracy (43.13% vs 13.62% baseline)" and cuts prompt tokens by more than 50%.

**MCP specification 2025-11-25: tools** — [MCP spec](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- Results can mix text, image (`{"type":"image","data":<base64>,"mimeType":"image/png"}`), audio, resource links and embedded resources. Content can carry `audience`/`priority` annotations.
- `structuredContent` may carry an `outputSchema`, and the result "SHOULD also return the serialized JSON in a TextContent block".
- Execution errors use `isError: true`, and input validation errors now count as execution errors. "Tool Execution Errors contain actionable feedback that language models can use to self-correct". "Clients SHOULD provide tool execution errors to language models to enable self-correction."
- Change since 2025-06-18: that version listed "Invalid arguments" as a protocol error — [MCP spec 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- Tools may declare `execution.taskSupport` — [MCP spec](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

**MCP specification: annotations and instructions**
- `ToolAnnotations` defaults: `readOnlyHint` false, `destructiveHint` true (meaningful only when not read-only), `idempotentHint` false, `openWorldHint` true. All are hints, untrusted unless the server is trusted — [schema.ts](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/schema/2025-11-25/schema.ts)
- `InitializeResult.instructions`: "Instructions describing how to use the server and its features … can be used by clients to improve the LLM's understanding of available tools" — [schema.ts](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/schema/2025-11-25/schema.ts)

**MCP specification: tasks (experimental since 2025-11-25)** — [MCP tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
- A `tools/call` can be task-augmented: the server returns a `CreateTaskResult` at once, and the client polls `tasks/get`, then fetches the result with `tasks/result`.
- Statuses are `working`, `input_required`, `completed`, `failed` and `cancelled`. `tasks/cancel` is best effort.
- Tasks are meant for "expensive computations and batch processing".

**MCP blog, "Server Instructions" (Ola Hungerford, 2025-11-03)** — [MCP blog](https://blog.modelcontextprotocol.io/posts/2025-11-03-using-server-instructions/)
- Use instructions for cross-tool relationships, workflows and constraints. Don't repeat tool descriptions, and keep them out of security-critical paths (use deterministic rules or hooks there).
- "No instructions are better than poorly written instructions."
- Small test of a three-step PR-review workflow: 17/20 runs followed it with instructions against 12/20 without. GPT-5-Mini went from 2/10 to 8/10; Claude Sonnet 4 went from 10/10 to 9/10.

**Claude Code (current docs)** — [Claude Code MCP docs](https://code.claude.com/docs/en/mcp)
- Tool search is on by default: "Only tool names and server instructions load at session start".
- "Claude Code truncates each tool description and each server's instructions at 2,048 characters by default. Keep them concise, and put critical details near the start." The limit can be changed with `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`.
- Image results (PNG, JPEG, GIF, WebP) are shown inline, possibly scaled down. The originals are saved to disk, and image tools count against `MAX_MCP_OUTPUT_TOKENS`.
- Output above 10,000 tokens raises a warning; the default limit is 25,000 tokens.
- `_meta["anthropic/alwaysLoad"]` loads a tool upfront.
- `_meta["anthropic/requiresUserInteraction"]` forces a permission prompt on every call.
- Resources are reachable through @-mentions and auto-provided list/read tools. Prompts become slash commands. `list_changed` refreshes tools without a reconnect.

### Inferences
- The "consolidate, don't wrap every endpoint" advice and the "code beats tool calls" result do not conflict for md-mcp: they apply at different layers.
  - Code (`run_md_python`) is the right vehicle for *composition*: many MD calls, loops, filtering before reply.
  - Curated tools are the right vehicle for *verification and safety-critical steps* (seeing, measuring, validating a seam plan, checkpointing), where poka-yoke and actionable errors pay off.
- md-mcp's server instructions come to about 1,990 characters by my count of `src/md_mcp/server.py` (lines 280–308; approximate, not run). That leaves roughly 60 characters before Claude Code's 2,048 cut. Roadmap P5.1 ("put the garment rules in the MCP server instructions") cannot be done by appending. Any rule placed after the cut never reaches the model.
  - In this very session, the Unity server's instructions are visibly cut off with "… [truncated]".
  - md-mcp needs a pointer near the start of its instructions to a guide tool or resource. Blender's PR #348 added a test that instructions stay within a size budget; md-mcp should add the same test.
- Since tool search defers definitions, adding a few garment tools costs little at session start. Their descriptions still have to fit 2,048 characters with the critical part first, and the instructions must say when to search for them.
- md-mcp currently sets no tool annotations (grep of `server.py`), so by the spec's defaults every tool is presented as destructive, non-idempotent and open-world. Honest hints:
  - `readOnlyHint`: `md_status`, `md_api`, `md_fabric_presets`.
  - `md_measure` exports an OBJ but changes no scene state, so `destructiveHint: false`.
  - `md_arrangement_points` adds and deletes a probe piece, so it is not read-only.
  - `md_snapshot` leaves the camera moved, so it is not read-only but is non-destructive.
  - A future `md_restore` is the case for `anthropic/requiresUserInteraction`.

### Gaps
- Claude Code's docs mention a newer protocol revision, "2026-07-28", which I did not read. Spec citations here are to 2025-11-25.
- I found no client-side data on how often models actually read MCP resources unprompted compared with calling a guide tool.

## Q6. Evidence of what works: look-at-the-result loops, validate/apply guardrails, curated against raw tools

### Takeaway
The strongest quantitative evidence supports two things:
1. **Interface guardrails and compact, purpose-built edit and view actions.** In SWE-agent's ablations, removing the linter cut resolve rate from 18.0% to 15.0%, and falling back to the shell instead of an edit command cut it to 10.3%.
2. **Closed render-inspect-revise loops for graphics code.** VIGA reports large gains over one-shot generation on BlenderGym and BlenderBench, and BlenderGym finds that the verifier is a key place to spend compute.

Server instructions and tool-use examples also show measured gains. I found **no controlled comparison of validate/apply patches against raw scripting inside a creative-app MCP server**. That evidence is circumstantial.

### Cited Findings
**SWE-agent (Yang et al., Princeton, arXiv 2405.15793 v3, 2024-11-11)** — [arXiv](https://arxiv.org/html/2405.15793)
- ACI principles: simple actions, compact operations, concise feedback, and guardrails, which "mitigate error propagation and hasten recovery".
- Ablations on SWE-bench Lite with GPT-4 Turbo (% resolved):

  | Variant | Resolved |
  |---|---|
  | Full system | 18.0% |
  | No linting guardrail | 15.0% |
  | No edit command (shell tools only) | 10.3% |
  | 30-line file viewer window | 14.3% |
  | Whole file shown | 12.7% |

**BlenderGym (Gu, Huang, Je, Yang, Guibas; CVPR 2025; arXiv 2504.01786)** — [arXiv](https://arxiv.org/abs/2504.01786)
- Even the best VLM system "struggles with tasks relatively easy for human Blender users".
- The verifier guiding generation can itself be improved by inference scaling, and compute is best split strategically between generation and verification.

**VIGA (Yin et al., incl. Darrell, Kanazawa, Black; arXiv 2601.11109, v3 2026-04-06)** — [arXiv](https://arxiv.org/abs/2601.11109)
- A "code-render-inspect loop" (write the program, render, compare with the target, edit) plus skills and a multimodal memory.
- Reports improvements over one-shot baselines of 35.32% on BlenderGym, 117.17% on SlideBench and 124.70% on BlenderBench. The abstract does not say whether these are relative gains.

**BlenderAlchemy (Huang, Yang, Guibas; arXiv 2404.17672)** — [arXiv](https://arxiv.org/abs/2404.17672)
- A "vision-based edit generator and state evaluator" searches edit sequences in Blender, plus "imagined" reference images to ground language.
- The abstract gives only qualitative evidence.

**LL3M (Lu et al., arXiv 2508.08228, 2025-08-11)** — [arXiv](https://arxiv.org/abs/2508.08228)
- Multi-agent Blender code generation in which agents "can automatically self-critique using code and visuals", grounded by BlenderRAG built from the Blender API docs.

**Guidance and examples**
- Server instructions: 85% against 60% workflow adherence overall, in a small n=40 test — [MCP blog](https://blog.modelcontextprotocol.io/posts/2025-11-03-using-server-instructions/)
- Tool use examples: 72% to 90% on complex parameters — [Anthropic](https://www.anthropic.com/engineering/advanced-tool-use)

**Practitioner signals**
- Blender's maintainers merged guidance into instructions because "the errors are deterministic, so retries can't fix them" — [PR #348](https://github.com/ahujasid/blender-mcp/pull/348)
- CoplayDev's skill prescribes a screenshot after every visual change and `read_console` after every script change — [workflows.md](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/unity-mcp-skill/references/workflows.md)
- Houdini's server advises checking cook state before reading geometry — [GitHub](https://github.com/oculairmedia/houdini-mcp)

**md-mcp's own evidence**
- The cape session lists, among the bridge's failures, that "Seeing the result is expensive", that MD's errors don't reach the caller, and that there was no structured seam read-back. A user judged the result unusable — [session report](../../../sessions/2026-10-11-vampire-cape.md) (repo file).

### Inferences
- For MD, "look at the result" is necessary but not sufficient. A picture does not show a hem sewn to a neckline (AGENTS.md: "A wrong seam builds cleanly"). The analogue of SWE-agent's linter is a structured seam ledger plus a plan validator: a guardrail that rejects a bad seam *before* it exists, since MD cannot remove one afterwards.
- BlenderGym's finding about spending compute on verification supports giving verification first-class tools (snapshot sheet, ledger diff, measurements) rather than leaving the model to compose them from raw calls each time. The cape session shows what that costs: OBJ export, ffmpeg tiling, file reads.
- The evidence does not argue for removing raw access. Code Mode and code execution with MCP argue that code composes better than tool calls. The right shape is raw tools for reach, plus a small set of curated guardrail and verification tools.

### Gaps
- I found no published A/B test of validate/apply patch tools against raw scripting in any DCC MCP server. Substance's server reports no success metrics.
- I did not read the full papers, so the LL3M refinement ablation and VIGA's absolute numbers are missing.

## Q7. Synthesis: how the servers balance raw code against structured tools, and which patterns md-mcp could adopt

### Takeaway
Across the field, the common mature shape is **a raw escape hatch plus a small set of curated tools that see, measure, validate and recover** (Blender, Houdini, uemcp, and CoplayDev with its opt-in `execute_code`). Only the security-first Substance server drops raw code entirely, and Epic exposes everything through discovery meta-tools.

md-mcp sits at the raw end, with good discovery (`md_api` matches Epic's and Anthropic's progressive disclosure) and four new read-back tools. The cape session's failures fall exactly where peers add curated tools: verification, validate-then-apply, checkpointing, error surfacing and guidance. That supports refining "nothing wrapped" to "nothing hidden": every MD call stays reachable raw, and a few garment-level guardrail tools are added on top.

### Cited Findings
**How each server balances raw and structured tools**

| Server | Raw code? | Structured tools | Verification | Undo/rollback | Guidance |
|---|---|---|---|---|---|
| Blender (MCP for Blender) | Yes, primary (`execute_blender_code`), optional safe mode | 8 others: look, scene info, assets, generation | `look`, inline PNG, angles sheet, shading modes, restores settings | None; "save your work" | Server instructions (moved from a prompt in PR #348) |
| Substance (MikeLi-28) | None | 29 allow-listed, Graph Patch 1.0 | Snapshot, node definitions, capabilities | Patch rollback of created nodes | One-line instructions |
| Unity (CoplayDev) | Opt-in group (`execute_code`) | 30 core of 50, `batch_execute` | Inline screenshots (640 px), `read_console`, `editor/state` | Editor undo/redo actions | Long instructions plus a shipped skill |
| Unreal (Epic) | Not stated | Toolsets behind 3 meta-tools | Not documented | Not documented | Docs advise small tools, structured returns |
| Houdini (oculairmedia) | Yes (`execute_code`, hscript) | 43 tools | Quad view, pane captures, `find_error_nodes` | Not documented | Not documented |
| md-mcp | Yes, primary (`md_call`, `run_md_python`) | `md_api` discovery plus 4 read-backs | `md_snapshot` inline (1024 px default), `md_measure` | None (MD has no undo) | About 1,990-character instructions |

Sources for the table:
- Blender: [README](https://github.com/ahujasid/blender-mcp); [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py); [PR #348](https://github.com/ahujasid/blender-mcp/pull/348)
- Substance: [GitHub](https://github.com/MikeLi-28/Substance-3D-Designer-MCP)
- Unity: [tool reference](https://coplaydev.github.io/unity-mcp/reference/tools); [manage_camera.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/services/tools/manage_camera.py); [manage_editor.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/services/tools/manage_editor.py); [workflows.md](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/unity-mcp-skill/references/workflows.md)
- Unreal: [Epic docs](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor)
- Houdini: [GitHub](https://github.com/oculairmedia/houdini-mcp)
- md-mcp: `src/md_mcp/server.py` and `AGENTS.md` in this repo.

**Patterns by area, with their sources**
1. **Verifying results**
   - One-call inline image, small by default: Blender 768 px — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py); Unity 640 px — [manage_camera.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/services/tools/manage_camera.py)
   - Several angles in one tiled image: Blender `angles`; Unity `surround`/`orbit`.
   - Shading modes that show structure (wireframe, x-ray): Blender.
   - Structured scene read-back with fields and limits: Blender `get_scene_info`.
   - Error-node discovery: Houdini `find_error_nodes` — [GitHub](https://github.com/oculairmedia/houdini-mcp)
   - The MCP spec allows image plus text in one result — [MCP spec](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
2. **Validate-then-apply**
   - Dry-run validator annotated read-only and idempotent; versioned, additive, bounded patch; every reference resolved before mutation; request-local aliases; rollback on failure — [architecture.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/architecture.md)
   - Hash precondition and stale-retry: Unity `get_sha` — [workflows.md](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/unity-mcp-skill/references/workflows.md)
   - Guardrail evidence: SWE-agent linter — [arXiv](https://arxiv.org/html/2405.15793)
3. **Capability discovery**
   - Available versus verified, by version — [architecture.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/architecture.md)
   - A status tool first: Blender `get_addon_status` — [server.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/src/blender_mcp/server.py)
   - Discovery meta-tools: Epic — [Epic docs](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor)
   - Live API reflection: Unity `unity_reflect` — [tool reference](https://coplaydev.github.io/unity-mcp/reference/tools)
4. **Snapshots, checkpoints and undo**
   - Neither Blender nor Substance offers a checkpoint tool. Blender says "ALWAYS save your work" — [README](https://github.com/ahujasid/blender-mcp)
   - Substance rolls back only the nodes a patch created — [security-model.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/security-model.md)
   - Unity exposes the editor's own undo and redo — [manage_editor.py](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/Server/src/services/tools/manage_editor.py)
5. **Host errors and console**
   - `read_console` after every script change — [workflows.md](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/unity-mcp-skill/references/workflows.md); its noise problem is issue #1434 — [issues](https://github.com/CoplayDev/unity-mcp/issues)
   - Full traceback returned: Blender — [addon.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/addon.py)
   - Stable error codes with safe messages and details, tracebacks in local logs: Substance — [protocol.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/protocol.md)
   - Blocking popups: Unity issue #1411 — [issues](https://github.com/CoplayDev/unity-mcp/issues)
   - `isError` for self-correction — [MCP spec](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
6. **Guidance delivery**
   - Server instructions, kept within a tested size budget — [PR #348](https://github.com/ahujasid/blender-mcp/pull/348)
   - A skill shipped in the repo: CoplayDev `unity-mcp-skill` — [workflows.md](https://raw.githubusercontent.com/CoplayDev/unity-mcp/main/unity-mcp-skill/references/workflows.md)
   - The 2,048-character truncation in Claude Code — [Claude Code MCP docs](https://code.claude.com/docs/en/mcp)
   - Instruction-writing advice — [MCP blog](https://blog.modelcontextprotocol.io/posts/2025-11-03-using-server-instructions/)
   - Examples in tool descriptions — [Anthropic](https://www.anthropic.com/engineering/advanced-tool-use)
7. **Main-thread responsiveness**
   - Queue plus main-thread timer: Blender — [addon.py](https://raw.githubusercontent.com/ahujasid/blender-mcp/main/addon.py)
   - Qt main-thread dispatcher that cancels a job only if it has not started: Substance — [architecture.md](https://github.com/MikeLi-28/Substance-3D-Designer-MCP/blob/main/docs/architecture.md)
   - Game thread, one call at a time: Epic — [Epic docs](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor)
   - Async job plus polling: Unity `run_tests`/`get_test_job` — [tool reference](https://coplaydev.github.io/unity-mcp/reference/tools)
   - MCP tasks, experimental — [MCP tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
   - Blender's main-thread downloads freeze its UI — [README](https://github.com/ahujasid/blender-mcp)
8. **Tool count and context budget**
   - Blender measured 5.5–6.9k tokens for 25–28 tools — [issue #347](https://github.com/ahujasid/blender-mcp/issues/347); it now shows 9 — [README](https://github.com/ahujasid/blender-mcp)
   - Unity: 50 tools, 30 visible by default — [tool groups](https://coplaydev.github.io/unity-mcp/guides/tool-groups)
   - Epic: 3 meta-tools — [Epic docs](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor)
   - Claude Code defers MCP tools by default — [Claude Code MCP docs](https://code.claude.com/docs/en/mcp)
   - Accuracy rises when fewer tools are presented — [Anthropic](https://www.anthropic.com/engineering/advanced-tool-use); [arXiv 2505.03275](https://arxiv.org/abs/2505.03275)

### Inferences
Concrete proposals for md-mcp, each tied to a roadmap item:

- **Verification (P0.3, P1).**
  - Extend `md_snapshot` to take a list of views and return *one* tiled sheet (front, back, side), as Blender `angles` and Unity `surround` do. That makes "check the drape" a single call, which the session report asked for.
  - Lower the default from 1024 px to about 640–768, in line with peers. Claude Code may downscale anyway.
  - Return a short text block alongside the image (the view numbers used, and seam and piece counts), so the picture and the ledger arrive together.
  - Add a 2D-board picture if any route works (`ExportSnapshot2D` returned `[]` in the session).
  - The camera cannot be restored, so say so in the result, as md-mcp already does.
- **Validate-then-apply seam plans (P2.1).** Copy Substance's shape, adjusted for MD:
  - `md_seam_plan` with `mode=validate` (read-only) or `mode=apply`, or two tools;
  - a versioned plan schema;
  - every piece, line and interval resolved against the live ledger before anything is sewn;
  - request-local aliases;
  - a scene fingerprint precondition, as Unity uses `get_sha`;
  - additive only;
  - apply takes a checkpoint first;
  - after sewing, read the ledger back and diff it against the plan;
  - restore the checkpoint on mismatch, since no MD call removes a seam.
- **Checkpoints (P0.1).** `md_checkpoint`/`md_restore` would go further than any peer, because MD has no undo while Blender and Unity have native undo. Annotate restore as destructive and consider `anthropic/requiresUserInteraction`. Have the seam-plan apply and any garment workflow create one automatically.
- **Capability discovery.** Report "verified on 2026.0.315" against "available but unmeasured" per `notes.py` entry and in `md_status`, as Substance does. Keep `md_api` as the discovery layer; Epic's meta-tools independently support that design.
- **Host errors (P0.2).**
  - Return MD's own messages with every reply, filtered of md-mcp's own transport lines (Unity #1434).
  - Use stable error codes with actionable text (Substance; Anthropic).
  - Mark execution failures `isError` so the model self-corrects.
  - When a call stalls, say explicitly that MD may be showing a modal dialog (Unity #1411, AGENTS.md).
- **Guidance (P5).**
  - The instructions are within about 60 characters of Claude Code's 2,048 cut. Move the most important line, "read `md_guide` before any mutation; checkpoint first", to the start.
  - Move the detailed gotchas into an `md_guide` tool, and possibly a resource too. A tool appears in the tool list and is more likely to be called than a resource is to be read (unmeasured).
  - Add a test that the instructions stay at or under 2,048 characters (Blender PR #348).
  - Put 1–5 worked examples in the seam-plan tool's description (Anthropic: 72% to 90%).
  - Ship the reliability skill with the plugin, as CoplayDev ships `unity-mcp-skill`.
- **Main thread (P0.0).**
  - md-mcp's pump-the-queue loop matches the field's invariant (serialised calls on the host's main thread).
  - For long `Simulate` runs, consider chunked steps with progress, or a job-and-poll tool (Unity `run_tests`; MCP tasks once clients support them), so no single reply outlasts the client timeout.
  - The listener's end-on-MD-exit bug has no direct peer precedent.
- **Annotations and budget.**
  - Add honest `readOnlyHint`/`destructiveHint`/`idempotentHint` values; the defaults currently mark every md-mcp tool destructive.
  - Keep garment tools few and consolidated, for example one seam tool with modes rather than five. Anthropic's "consolidate" advice and Blender's shrink from 28 tools to 9 both point that way.

### Gaps
- None of the peers has solved MD-specific problems: 3D placement without a transform setter, seam removal, fabric physics setters. The peer review cannot answer the roadmap's P3 or P4.
- I found no measured success rates for any DCC MCP server, which would show whether curated tools beat raw scripting for that domain. The recommendation above rests on design convergence and adjacent evidence (SWE-agent, BlenderGym, VIGA), not on a direct trial.
