# pyright: reportUnusedCallResult=false
import json
import socket
import struct
from typing import TYPE_CHECKING, final

from dmn.api.dispatcher import CommandDispatcher
from dmn.api.protocol import Request, Response
from dmn.protocols import EventLoopProtocol
from dmn.tasks import Interest

if TYPE_CHECKING:
    from dmn.tasks.pty_task import PTYTask


@final
class HandleSockConnectionTask:
    MAX_LINE = 8 * 1024
    """
    Handle a single socket connection.
    """

    def __init__(
        self,
        sock: socket.socket,
        dispatcher: CommandDispatcher,
        pty_task: "PTYTask",
        *,
        loop: EventLoopProtocol,
    ):
        self.loop = loop
        self.sock: socket.socket = sock
        self.dispatcher = dispatcher
        self._pty_task = pty_task
        self.rbuf = bytearray()
        self.wbuf = bytearray()
        self._attached = False
        self._watching = False
        self._pending_nul = False

    def fileno(self) -> int:
        return self.sock.fileno()

    def interests(self) -> int:
        ev = Interest.READ
        if self.wbuf:
            ev |= Interest.WRITE
        return ev

    def on_readable(self) -> None:
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self.loop.unregister(self)
            return

        if self._attached:
            self._handle_attached_data(data)
            return

        if self._watching:
            # Read-only mode: discard any input from the client
            return

        self.rbuf.extend(data)
        while True:
            nl = self.rbuf.find(b"\n")
            if nl < 0:
                if len(self.rbuf) > self.MAX_LINE:
                    self.loop.unregister(self)
                break
            line = bytes(self.rbuf[:nl]).strip()
            del self.rbuf[: nl + 1]
            if not line:
                continue
            self._handle_line(line)

    def _handle_line(self, line: bytes):
        if len(line) > self.MAX_LINE:
            self._enqueue_response(b'{"ok": false, "error":"line too long"}')
            return
        try:
            obj = json.loads(line.decode())
            req_type: str = obj.get("type", "")
            payload = obj.get("payload", {})
            request = Request(type=req_type, payload=payload)
        except Exception:
            response = Response(ok=False, error="invalid json")
            self._enqueue_response(json.dumps(response.to_dict()).encode())
            return

        if request.type == "ATTACH":
            self._attach()
            return

        if request.type == "WATCH":
            self._watch()
            return

        response = self.dispatcher.dispatch(request)
        self._enqueue_response(json.dumps(response.to_dict()).encode())

    def _attach(self) -> None:
        self._attached = True
        self._pty_task.add_output_listener(self._on_pty_output)
        # Send any remaining rbuf as raw input to PTY
        if self.rbuf:
            self._handle_attached_data(bytes(self.rbuf))
            self.rbuf.clear()
        response = Response(ok=True, data={})
        self._enqueue_response(json.dumps(response.to_dict()).encode())

    def _watch(self) -> None:
        self._watching = True
        self._pty_task.add_output_listener(self._on_pty_output)
        response = Response(ok=True, data={"mode": "watch"})
        self._enqueue_response(json.dumps(response.to_dict()).encode())

    # Resize control: \x00R + cols_hi cols_lo rows_hi rows_lo (6 bytes total)
    _RESIZE_PREFIX = b"\x00R"
    _RESIZE_LEN = 6  # \x00 R col_h col_l row_h row_l

    def _handle_attached_data(self, data: bytes) -> None:
        if self._pending_nul:
            self._pending_nul = False
            if len(data) >= 5 and data[0:1] == b"R":
                cols, rows = struct.unpack("!HH", data[1:5])
                self._pty_task.resize(cols, rows)
                data = data[5:]
                if not data:
                    return
            else:
                # Was a real NUL byte, forward it
                self._pty_task.write(b"\x00")

        # Scan for resize sequences in the data
        while True:
            idx = data.find(b"\x00")
            if idx < 0:
                break
            # Check if it's at the end (might be split)
            if idx == len(data) - 1:
                self._pending_nul = True
                data = data[:idx]
                break
            # Check for resize marker
            if data[idx + 1 : idx + 2] == b"R" and len(data) >= idx + self._RESIZE_LEN:
                # Forward everything before the resize
                if idx > 0:
                    self._pty_task.write(data[:idx])
                cols, rows = struct.unpack("!HH", data[idx + 2 : idx + 6])
                self._pty_task.resize(cols, rows)
                data = data[idx + 6 :]
            else:
                # Not a resize — forward up to and including the \x00, continue scanning
                self._pty_task.write(data[: idx + 1])
                data = data[idx + 1 :]

        if data:
            self._pty_task.write(data)

    def _on_pty_output(self, data: bytes) -> None:
        self.wbuf.extend(data)
        self.loop.modify(self, self.interests())

    def _enqueue_response(self, b: bytes):
        self.wbuf.extend(b + b"\n")
        self.loop.modify(self, self.interests())

    def on_writable(self) -> None:
        if not self.wbuf:
            self.loop.modify(self, Interest.READ)
            return

        try:
            n = self.sock.send(self.wbuf)
            if n:
                del self.wbuf[:n]
        except BlockingIOError:
            # try again later
            return
        except OSError as e:
            self.on_error(e)
            return

        self.loop.modify(self, self.interests())

    def on_error(self, exc: BaseException) -> None:
        self.loop.unregister(self)

    def on_close(self) -> None:
        if self._attached or self._watching:
            self._pty_task.remove_output_listener(self._on_pty_output)
        try:
            self.sock.close()
        except Exception:
            pass
