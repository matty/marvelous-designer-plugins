# ADR 001: the bridge is a socket MD hosts, served from a timer on MD's main thread

Status: accepted. Revised 2026-10-11, when the never-returning serve loop was replaced by a
Windows timer.

## Context

md-mcp runs as its own process, launched by an MCP client. Marvelous Designer's Python
API exists only inside MD's embedded interpreter (CPython 3.11.8 in 2026.0.315). Something
has to carry requests across.

What MD offers, measured on 2026.0.315:

- **Nothing to dial into.** There is no server in MD for an outside process to connect
  to. Importing `REST_API` raises `ModuleNotFoundError`, and MD's own documentation
  describes that module as an outbound HTTP client (`CallRESTPut`,
  `CallbackRestRequest`), not a server.
- **A Python editor and a Plugin tab** that run a `.py` file on MD's main thread.
- **No callbacks from the API.** No idle, timer or event hook.
- **Python threads freeze when a script returns.** MD releases the GIL only while a
  script is running: a thread ticked 20 times while its script slept two seconds, and not
  once afterwards. A listener on a daemon thread binds its port, accepts connections into
  the backlog, and answers nothing.

## Decision

MD hosts a loopback socket and md-mcp connects to it. The listener
(`plugin/md_mcp_listener.py`) binds `127.0.0.1:5121` and speaks one JSON object per line
each way, one request per connection: `ping`, `scene`, `run` (a script, with its output
and the scene read back), `shutdown`.

The listener is served by a `user32.SetTimer` thread timer on MD's main thread. Start MCP
binds the port, arms the timer and returns; Windows calls the timer from MD's own message
loop, and each tick accepts, reads and answers what is ready, then gives the thread back.
A tick never runs inside another, so one request at a time executes inside MD. Stop MCP,
a `shutdown` request, or MD closing ends it. Measured 2026-10-11: 50 pings at a 15.9 ms
median round trip, MD responsive between requests, and MD exiting normally when closed.

Every MD call happens on the main thread, the only one MD documents as safe. While a
request executes MD is unresponsive for exactly as long as the work takes.

md-mcp's tools **fail** when nothing is listening, rather than returning an empty result:
"no patterns found" read as success would say MD is connected and idle, which is the
opposite of true.

## Options rejected

| Option | Why not |
| --- | --- |
| A listener on a background thread | Freezes the moment the script that started it returns: connected, and dead. |
| A script that never returns, serving in a loop and pumping MD's message queue (the design until 2026-10-11) | Worked, and held MD's main thread for the whole session: closing MD left the process running, every other script ran nested inside the loop, and a second loop of any kind (a window's `mainloop()`) would have starved the first. |
| A control window with Start and Stop | Needed the loop above to drive it; replaced by two Plugin tab entries. MD's Qt UI cannot be docked into from Python, and its `tkinter` cannot open a window (no Tcl library ships with MD). |
| File drop: md-mcp writes a script, MD polls a folder | Still needs something polling inside MD, which is the same problem as the socket, with worse latency and clean-up. |
| Driving MD's GUI | Not an API; brittle across builds and screens. |

## Consequences

- Anything that can reach 127.0.0.1 on the port can run Python inside MD: the same trust
  boundary as MD's own script menu. The port opens only when someone clicks Start MCP.
- A modal dialog in MD holds the main thread, so a request waiting on MD cannot be
  answered until it is dismissed. md-mcp watches for one from outside MD and gives up on
  the reply in about two seconds, naming it (`md_mcp/dialogs.py`).
- A request abandoned on timeout still runs to the end inside MD. Replies say so, and the
  scene is read back after every `run`.
- MD keeps a registered plugin's path for good, so an old listener can answer
  confidently. `ping` carries the listener's version, build and path.
