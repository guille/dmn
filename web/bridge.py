# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp"]
# ///
"""Web terminal bridge — serves ghostty-web and bridges WebSocket to dmn ATTACH."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import struct
from pathlib import Path

from aiohttp import web

log = logging.getLogger("bridge")

DEFAULT_SESSION = "default"
_RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")


def sock_path(session: str = DEFAULT_SESSION) -> str:
    return os.path.join(_RUNTIME_DIR, f"dmn-{session}.sock")


WEB_DIR = Path(__file__).parent
DIST_DIR = WEB_DIR / "node_modules" / "ghostty-web" / "dist"


async def handle_index(request: web.Request) -> web.Response:
    session = request.app["session"]
    html = (WEB_DIR / "index.html").read_text()
    # Inject default session as a data attribute
    html = html.replace(
        'id="session-input"',
        f'id="session-input" data-default="{session}"',
    )
    return web.Response(text=html, content_type="text/html")


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    session: str = request.query.get("session", request.app["session"])
    path = sock_path(session)

    try:
        reader, writer = await asyncio.open_unix_connection(path)
    except (ConnectionRefusedError, FileNotFoundError) as e:
        await ws.send_str(
            f"\x1b[31mFailed to connect to dmn session '{session}': {e}\x1b[0m\r\n"
        )
        await ws.close()
        return ws

    # ATTACH or WATCH handshake
    readonly = request.query.get("readonly", "") == "1" or request.app["force_readonly"]
    cmd = "WATCH" if readonly else "ATTACH"
    writer.write(json.dumps({"type": cmd}).encode() + b"\n")
    await writer.drain()

    # Read response (newline-delimited JSON)
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    except asyncio.TimeoutError:
        await ws.send_str("\x1b[31mATTACH handshake timed out\x1b[0m\r\n")
        writer.close()
        await ws.close()
        return ws

    try:
        resp = json.loads(line)
    except json.JSONDecodeError, ValueError:
        await ws.send_str(f"\x1b[31mInvalid handshake response: {line!r}\x1b[0m\r\n")
        writer.close()
        await ws.close()
        return ws

    if not resp.get("ok"):
        await ws.send_str(f"\x1b[31mATTACH rejected: {resp}\x1b[0m\r\n")
        writer.close()
        await ws.close()
        return ws

    log.info("WebSocket client attached to session '%s'", session)

    # Send initial resize from query params
    try:
        cols = int(request.query.get("cols", "80"))
        rows = int(request.query.get("rows", "24"))
        writer.write(b"\x00R" + struct.pack("!HH", cols, rows))
        await writer.drain()
    except ValueError, OSError:
        pass

    # Bridge tasks
    async def sock_to_ws() -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                await ws.send_str(data.decode("utf-8", errors="replace"))
        except ConnectionResetError, asyncio.CancelledError:
            pass

    async def ws_to_sock() -> None:
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    if readonly:
                        continue
                    text = msg.data
                    # Handle resize messages
                    if text.startswith('{"type":"resize"'):
                        try:
                            obj = json.loads(text)
                            cols = int(obj["cols"])
                            rows = int(obj["rows"])
                            # Send \x00R + cols(u16 BE) + rows(u16 BE)
                            writer.write(b"\x00R" + struct.pack("!HH", cols, rows))
                            await writer.drain()
                        except json.JSONDecodeError, KeyError, ValueError:
                            pass
                        continue
                    writer.write(text.encode("utf-8"))
                    await writer.drain()
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        except ConnectionResetError, asyncio.CancelledError:
            pass

    t1 = asyncio.create_task(sock_to_ws())
    t2 = asyncio.create_task(ws_to_sock())

    try:
        done, pending = await asyncio.wait(
            {t1, t2}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    finally:
        t1.cancel()
        t2.cancel()
        writer.close()
        await ws.close()
        log.info("WebSocket client detached")

    return ws


def create_app(session: str, *, force_readonly: bool = False) -> web.Application:
    app = web.Application()
    app["session"] = session
    app["force_readonly"] = force_readonly

    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)
    app.router.add_static("/dist/", DIST_DIR, show_index=False)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="dmn web terminal bridge")
    parser.add_argument(
        "-s", "--session", default=DEFAULT_SESSION, help="dmn session name"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=9001, help="listen port (default: 9001)"
    )
    parser.add_argument(
        "--force-readonly",
        action="store_true",
        help="force all connections to read-only (WATCH) mode",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app = create_app(args.session, force_readonly=args.force_readonly)
    log.info(
        "Serving on http://%s:%d (session: %s%s)",
        args.host,
        args.port,
        args.session,
        ", read-only" if args.force_readonly else "",
    )
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
