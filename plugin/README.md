# The Marvelous Designer side of the bridge

One entry in MD's Plugin tab, **Start/Stop MCP**, which runs `md_mcp_listener.py`:

| Click | Does |
|---|---|
| While stopped | Opens a loopback port, arms a timer that serves it, and returns. A message box appears only if something needs attention. |
| While listening | Kills the timer and closes the port, and says so in a box. MD carries on as it was. |

There is no window, and nothing holds MD's main thread between requests: closing MD
ends the bridge with it. MD's plugin format is a `.py` file and nothing else — no
manifest, no bundle, no entry point — and Plug-in Manager adds them **one at a time**,
so the plugin owns exactly this one entry. `md_mcp_stop.py` ships beside it and still
stops the bridge, for anyone with a Stop MCP entry from an earlier version.

## Installing

From a release: unzip and run `install.ps1`.

```powershell
.\install.ps1                        # -> %LOCALAPPDATA%\md-mcp
.\install.ps1 -Destination D:\tools\md-mcp
.\install.ps1 -Uninstall
```

It verifies every file against the package's `manifest.json` before copying anything,
copies the scripts somewhere MD can keep pointing at, and prints — and copies to the
clipboard — the two lines to run once inside MD. From a checkout, `plugin/install.ps1`
does the same thing without the verification, since there is no manifest to verify
against.

**The install does not register anything.** That happens inside MD, on the first run.

## Running it

Once, in **Main Menu → Plugins → Python Editor**: paste the two lines the installer
printed (they are also in `bootstrap.txt` beside the installed listener) and press
**Run**.

```python
path = r"C:\Users\you\AppData\Local\md-mcp\md_mcp_listener.py"
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), {"__file__": path, "__name__": "__main__"})
```

Paste those two lines rather than the file's contents. MD keeps its own editor buffer,
so it is easy to run a stale copy without noticing — and a pasted body has no
`__file__`, so it cannot register itself either. It says so rather than registering
something that would fail when clicked.

That run starts the bridge, **finishes**, and adds **Start/Stop MCP** to the Plugin tab.
Every session after it is one click there.

**Nothing listens until somebody clicks Start/Stop MCP.** `run` executes arbitrary
Python inside MD, so the port opens because it was asked for, never as a side effect.
What stops it is clicking the entry again, a `shutdown` over the socket, or MD closing.

Upgrading: an entry you added by hand for the listener (MD names it after the file,
`md_mcp_listener`) runs the same file, so it starts and stops the bridge too; MD will not
retitle a path it already has. An old **Start MCP** entry does the same, and an old
**Stop MCP** entry still stops it.

## Why it is built the way it is

**A Windows timer, not a loop and not a thread.** MD runs Python threads only while a
script is running: the moment one returns, every thread freezes, so a listener on a
background thread binds its port and then answers nothing. A script that never returns
would hold MD's main thread for the whole session, so closing MD would leave the process
running. A `user32.SetTimer` thread timer is called from MD's own message loop, on MD's
main thread, after the script that armed it has returned. Measured inside MD 2026.0.315:
100 requests at a 31 ms median round trip, MD responsive throughout, and the process
gone when MD was closed.

**Each tick takes what is ready and gives the thread back.** It accepts waiting
connections, reads what has arrived, answers every request that is complete, and
returns. A peer that has sent nothing is dropped after a second and never holds anybody
else up. While a request is executing MD is unresponsive for as long as the work takes,
as it is when Simulate is clicked.

**A tick never runs inside another.** An MD call that pumps the message queue — a modal
dialog does — lets the timer fire inside the request that made it. That inner tick
returns at once, so one request at a time executes inside MD, which is not re-entrant.

**One entry, because MD would not take two.** Registering `md_mcp_stop.py` beside a
registered listener was refused as "already registered", for a script MD had never had,
and no Stop entry appeared (MD 2026.0.315, 2026-10-12, in MD's plugin log): the bridge
could be started and not stopped. So the listener's own entry toggles. Each run is a
fresh namespace, so the running listener leaves its state on `builtins`, the one place
two runs share, with a function the next click (or `md_mcp_stop.py`) calls.

## Registering

`utility_api.RegisterPythonScript(path, title)` adds an entry — **path first**. MD's
plugin log, `C:\Users\Public\Documents\MarvelousDesigner\New Assets\api_plugin_log.txt`,
says why any registration failed. MD declines a path it already has, whatever the
title — so an install whose listener was added by hand keeps that entry, under its old
title — and it refused a second script from the listener's folder.

The call is declared `-> bool` and that bool is checked: a `False` is reported and never
written to `%LOCALAPPDATA%\md-mcp\registered.json`, because an entry recorded as
registered is one nobody ever offers again. MD's own list is
`C:\Users\Public\Documents\MarvelousDesigner\Plugins\pluginSettings.json`, rewritten
when MD exits.

Asked once per (path, name). Delete that file to ask again, or set
`MD_MCP_NO_REGISTER=1` to never ask. **Nothing in the API unregisters** — the only
measured way to take an entry out is Plug-in Manager, and that is why this plugin
registers one entry and not more.

If MD refuses, add it by hand — **Plugin** tab → **Plug-in Manager** → **+ ADD** → the
installed `md_mcp_listener.py`.

## Which listener is answering

`ping` carries the plugin's version, the commit it was built from and the path it was
loaded from, and `md_status` reports all three. That last one matters: **MD holds a
registered plugin's path for good**, so an install from two versions ago keeps
answering, exactly as confidently as a current one, and every symptom of it looks like
md-mcp getting MD wrong. A checkout reports itself as `0.0.0+source`. After installing a
new build, click **Start/Stop MCP** twice — stop, then start — which reads the file
afresh.

## Building the package

```powershell
python tools/build_plugin.py          # -> dist/md-mcp-plugin-<version>.zip
```

The zip holds the stamped listener, the Stop script, this README, `install.ps1` and a
`manifest.json` with a SHA-256 of each. The same files are left unpacked beside it, so a
build can be pointed at without unzipping anything. Rebuilt from the same commit it
produces the same bytes, so a published hash can be reproduced rather than trusted.

The build refuses to package a script MD could not run: syntax outside CPython 3.11
(what MD 2026.0.315 embeds, whatever the build machine is running), an import of
anything that is neither standard library nor a module MD injects, or a listener with no
version stamp. Each of those otherwise passes every test on the build machine and fails
on the first click inside MD.

`.github/workflows/ci.yml` runs the tests on Windows and Linux and rebuilds the package
to check the bytes match. `.github/workflows/release.yml` publishes the zip and the
md-mcp wheel together on a `v*` tag, refusing to if the tag and `pyproject.toml`
disagree about the version — the two halves of a bridge must carry the same number.

## The protocol

One JSON object per line in, one per line out, then the connection closes. One request
at a time executes inside MD, oldest first, and a peer that connects and says nothing is
dropped after a second without holding up anybody else.

| Request | Reply |
|---|---|
| `{"op": "ping"}` | MD's version and process id, and the listener's version and path |
| `{"op": "scene"}` | the pieces, seam groups, avatars and fabrics, read from MD |
| `{"op": "run", "source": "..."}` | whatever the script printed, plus the resulting scene |
| `{"op": "shutdown"}` | stops the listener, as a second click on the entry does |

A script that raises comes back as `{"ok": false}` with its traceback, and the listener
keeps serving — a failed call does not cost you an MD restart.
This includes `sys.exit()`: its `SystemExit` is reported to the caller without stopping
the listener. An unavailable optional scene readback is returned in `readback_errors`
instead of making an otherwise successful script look as though it failed.

`run` executes arbitrary Python inside MD, so anything that can reach 127.0.0.1 on this
port can drive MD. That is the same trust boundary as MD's own script menu. Set
`MD_MCP_PORT` to move it, in **both** MD's environment and md-mcp's.

## Why a socket

MD cannot be dialled into by anything it does not host itself — this build has no
`REST_API` module at all, and MD's documentation describes `REST_API` as an outbound HTTP
client, not a server. So MD hosts the socket and md-mcp connects to it.
