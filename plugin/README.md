# The Marvelous Designer side of the bridge

One file: `md_mcp_listener.py`. It runs **inside** MD, binds a loopback socket and
serves md-mcp for the rest of the MD session, holding MD's main thread and pumping MD's
message queue so MD stays usable meanwhile. It also puts up a small window with Start
and Stop in it, so none of that has to be done by pasting code.

MD's plugin format is a `.py` file and nothing else — no manifest, no bundle, no entry
point, and Plug-in Manager adds them **one at a time**. So everything is in the one
file, and the plugin owns exactly **one** entry in the Plugin tab.

## Installing

From a release: unzip and run `install.ps1`.

```powershell
.\install.ps1                        # -> %LOCALAPPDATA%\md-mcp
.\install.ps1 -Destination D:\tools\md-mcp
.\install.ps1 -Uninstall
```

It verifies every file against the package's `manifest.json` before copying anything,
copies the listener somewhere MD can keep pointing at, and prints — and copies to the
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

That run opens the control window and adds **md-mcp** to the Plugin tab. Every session
after it is one click there.

**It does not start listening on its own.** The window comes up idle and **Start** opens
the port: `run` executes arbitrary Python inside MD, so it takes a press rather than a
side effect of opening a menu entry. The exception is a window that could not be built
at all — then there is nothing to press, so the bridge opens itself and says so in a
message box.

```
┌ md-mcp ─────────────────────────┐
│ Listening on 127.0.0.1:5121     │
│ md-mcp 0.3.0 · 12 request(s)    │
│ listening on 127.0.0.1:5121     │
│ C:\…\md-mcp\md_mcp_listener.py  │
│  [ Start ]  [ Stop ]            │
└─────────────────────────────────┘
```

**Closing the window does not stop the bridge.** It is a control panel, not a leash —
clicking **md-mcp** in the Plugin tab again brings it back. What stops the bridge is
**Stop**, MD closing, or a `shutdown` over the socket. Closing the window while it is
*not* serving ends the script, because a script with nothing left to do should return:
MD only runs Python while one is running, so a loop holding the main thread for nobody
costs MD and buys nothing.

## Why it is built the way it is

**The window has no event loop of its own.** MD runs Python only while a script is
running, so everything this plugin does happens inside one script that does not return.
That one loop pumps MD's message queue, refreshes the window, and accepts connections.
A window with its own `mainloop()` would hold the thread and the bridge would answer
nothing for as long as the window was open.

**One window, on `user32`, with no dependencies.** `RegisterClassW`,
`CreateWindowExW` and two `BUTTON` children through `ctypes` — the same DLL this file
already calls to pump MD's message queue. Nothing to install, nothing to go missing,
and the same window on every machine.

**It does not look like MD, and that was checked rather than assumed.** MD's UI is Qt
6.10.3, and MD ships no Python binding for it. A `PySide6-Essentials==6.10.3` dropped
into MD's interpreter *does* work — `QApplication.instance()` inside MD hands back MD's
own application, so one Qt, real widgets, MD's palette and Inter font applied for free
— and the result still reads as a stock Qt dialog rather than MD. Ninety megabytes
inside MD's own interpreter for a window that is merely dark is not a trade worth
making, so it was taken out again. `docs/md-conventions.md` has everything that was
measured, for whoever wants to reopen the question.

**Docking is not on offer at all**, with or without a binding. `RegisterWidget(pointer)`
accepts a real widget pointer and does nothing visible with it; `UpdateCloStyleForPlugIn`
will not convert a shiboken widget; `GetStyleSheetCodeForWidget` wants a `CloWidgetType`
that MD exposes nowhere, so it cannot be called at all. A floating window is as far in
as MD lets Python go.

MD's `tkinter` is a trap worth naming: it imports, and `Tk()` then raises
`TclError: Can't find a usable init.tcl`, because MD ships Tk's DLLs without any of
Tcl's script library.

**Watched working inside MD**, on 2026.0.315: `WM_COMMAND` posted to the window from
another process ran its window procedure on MD's main thread, Stop closed the port and
the bridge stopped answering, Start reopened it, and the labels followed each change.
If the window will not open, the plugin says so in a message box and serves without it.

It is laid out in 96-dpi units and scaled to the display it opens on (MD reported 120
dpi here). In raw pixels the first thing lost is the right-hand end of the path — the
one line that says which install is answering.

**Clicking the entry twice does not stop anything.** A plugin clicked while the loop is
running executes *nested* inside it, on MD's main thread — measured on 2026.0.315 — so
all a second run can do is leave a flag the loop reads when the click returns. That flag
is "show the window", not "stop": a button named after the plugin must not quietly take
the bridge down. Running `md_mcp_listener.py` itself again with no window available
still stops it, and says so.

## Registering

`utility_api.RegisterPythonScript(name, path)` adds the entry, and it appears without
restarting MD — measured on 2026.0.315, from a **top-level Run** in the Python Editor.

**It answers `False` when it is called from a script running inside another one**, which
is what a run over the bridge is: four attempts, wide and narrow forms, names with and
without punctuation, every one declined and none appeared. So register from a plain Run
with nothing else going. The call is declared `-> bool` and that bool is checked: a
`False` is reported and never written to `%LOCALAPPDATA%\md-mcp\registered.json`,
because an entry recorded as registered is one nobody ever offers again.

Asked once per (path, name). Delete that file to ask again, or set
`MD_MCP_NO_REGISTER=1` to never ask. **Nothing unregisters** — MD has no call that
removes an entry, so the only way to take one out is Plug-in Manager, and that is why
this plugin registers one entry and not three.

If MD refuses, add it by hand — **Plugin** tab → **Plug-in Manager** → **+ ADD** → the
installed `md_mcp_listener.py` → name it → **OK**.

## Which listener is answering

`ping` carries the plugin's version, the commit it was built from and the path it was
loaded from, and `md_status` reports all three. That last one matters: **MD holds a
registered plugin's path for good**, so an install from two versions ago keeps
answering, exactly as confidently as a current one, and every symptom of it looks like
md-mcp getting MD wrong. A checkout reports itself as `0.0.0+source`.

## Building the package

```powershell
python tools/build_plugin.py          # -> dist/md-mcp-plugin-<version>.zip
```

The zip holds the stamped listener, this README, `install.ps1` and a `manifest.json`
with a SHA-256 of each. The same files are left unpacked beside it, so a build can be
pointed at without unzipping anything. Rebuilt from the same commit it produces the same
bytes, so a published hash can be reproduced rather than trusted.

The build refuses to package a listener MD could not run: syntax outside CPython 3.11
(what MD 2026.0.315 embeds, whatever the build machine is running), an import of
anything that is neither standard library nor a module MD injects, or a missing version
stamp. Each of those otherwise passes every test on the build machine and fails on the
first click inside MD.

`.github/workflows/ci.yml` runs the tests on Windows and Linux and rebuilds the package
to check the bytes match. `.github/workflows/release.yml` publishes the zip and the
md-mcp wheel together on a `v*` tag, refusing to if the tag and `pyproject.toml`
disagree about the version — the two halves of a bridge must carry the same number.

## The protocol

One JSON object per line in, one per line out, then the connection closes. One request
at a time executes inside MD — by construction, since there is one loop on one thread —
and a peer that connects and says nothing is dropped after five seconds while the loop
keeps pumping, so it cannot stall real work.

| Request | Reply |
|---|---|
| `{"op": "ping"}` | MD's version and process id, and the listener's version and path |
| `{"op": "scene"}` | the pieces, seam groups, avatars and fabrics, read from MD |
| `{"op": "run", "source": "..."}` | whatever the script printed, plus the resulting scene |
| `{"op": "shutdown"}` | stops the listener |

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
`REST_API` module at all. The reasoning, the options rejected, and why the loop is on
MD's main thread rather than a daemon thread are in `docs/adr/001-bridge-transport.md`.
