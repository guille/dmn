# dmn

A lightweight PTY daemon. Spawns a shell in a pseudo-terminal and exposes it over a Unix socket, allowing multiple clients to send commands, attach interactively, or watch the output read-only.

You can read more about how I built this and watch a demo video showcasing dmn [in my blog](https://guille.github.io/posts/dmn/).

## Quick start

Install via pip/uv:

> [!IMPORTANT]
> The package name is `dmn-pty` not `dmn`. I do not maintain that project!

```bash
pip install dmn-pty
# or uv
uv tool install dmn-pty

# Start the daemon (default session)
dmn

# attach to it
dmn-attach

# Or with a named session and custom shell
dmn --session work --shell /bin/bash
```

Or from the repo's root:
```bash
# Start the daemon (default session)
uv run -m dmn

# attach to it
uv run -m dmn.attach

# Or with a named session and custom shell
uv run -m dmn --session work --shell /bin/bash
```

## Clients

### `scripts/client.py` — command execution

Send one-off commands to the running shell (starts a REPL):

```bash
python scripts/client.py -s work
> echo hi # gets sent to the daemonized session
```


### `web/bridge.py` — web terminal

A WebSocket bridge that serves a browser-based terminal using [ghostty-web](https://github.com/coder/ghostty-web)

```bash
cd web && npm install
uv run --script web/bridge.py --session work
# Open http://127.0.0.1:9001 in a browser
```

The web UI has session switching, connect/disconnect controls, and a read-only mode. The bridge also supports `--force-readonly` for sharing a terminal view without allowing input.

## How it works

dmn uses a hand-rolled event loop built on a [selector](https://docs.python.org/3/library/selectors.html). A single-threaded loop manages the PTY, stdin/stdout, a Unix server socket, and all connected clients.

### Protocol

Clients connect to a Unix socket (`$XDG_RUNTIME_DIR/dmn-{session}.sock`) and send newline-delimited JSON:

**EXEC** — send input to the shell:
```json
{"type": "EXEC", "payload": {"command": "ls -la\n"}}
```

**ATTACH** — switch to raw bidirectional mode:
```json
{"type": "ATTACH"}
```
After the `{"ok": true}\n` response, the connection is raw: bytes from the client go to the PTY, bytes from the PTY go to the client.

**WATCH** — read-only attach (output only, input discarded):
```json
{"type": "WATCH"}
```

### Resize (in ATTACH mode)

Terminal size changes are sent in-band as a 6-byte escape sequence:

```
\x00 R cols_hi cols_lo rows_hi rows_lo
```

## Requirements

- Python 3.14+
- Node/npm (for the web client's ghostty-web dependency)
