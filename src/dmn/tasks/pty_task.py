import fcntl
import os
import struct
import termios
from collections.abc import Callable
from typing import final

from dmn.exceptions import BufferFullError
from dmn.protocols import EventLoopProtocol
from dmn.tasks import Interest, Sink

OutputListener = Callable[[bytes], None]

HIGH_WATER = 256 * 1024  # 256 KB
LOW_WATER = 64 * 1024  # 64 KB
MAX_BUFFER = 1024 * 1024  # 1 MB


@final
class PTYTask:
    """
    Manages IO for the PTY Master FD.
    - Reads from PTY and sends to `stdout_sink`.
    - Accepts writes (via `write()`) and buffers them to PTY.
    """

    def __init__(self, pty_fd: int, *, sink: Sink, loop: EventLoopProtocol):
        self.loop = loop
        self.fd: int = pty_fd
        self._stdout_sink = sink
        self._wbuf = bytearray()
        # Flow control as a sink (stdin → PTY direction)
        self._paused = False
        self._on_pause: Callable[[], None] | None = None
        self._on_drain: Callable[[], None] | None = None
        # Flow control as a source (PTY → stdout direction)
        self._read_paused = False
        self._output_listeners: list[OutputListener] = []

    def add_output_listener(self, listener: OutputListener) -> None:
        self._output_listeners.append(listener)

    def remove_output_listener(self, listener: OutputListener) -> None:
        try:
            self._output_listeners.remove(listener)
        except ValueError:
            pass

    def fileno(self) -> int:
        return self.fd

    def interests(self) -> int:
        mask = 0
        if not self._read_paused:
            mask |= Interest.READ
        if self._wbuf:
            mask |= Interest.WRITE
        return mask

    def set_flow_control(
        self, *, on_pause: Callable[[], None], on_drain: Callable[[], None]
    ) -> None:
        self._on_pause = on_pause
        self._on_drain = on_drain

    def pause_reading(self) -> None:
        self._read_paused = True
        self.loop.modify(self, self.interests())

    def resume_reading(self) -> None:
        self._read_paused = False
        self.loop.modify(self, self.interests())

    def write(self, data: bytes) -> None:
        """
        Sink interface implementation.
        Called by StdinReadTask or UnixServerTask to send data TO the PTY.
        """
        if not data:
            return
        if len(self._wbuf) + len(data) > MAX_BUFFER:
            raise BufferFullError("PTY write buffer full")
        self._wbuf.extend(data)
        self.loop.modify(self, self.interests())

        if not self._paused and len(self._wbuf) > HIGH_WATER:
            self._paused = True
            if self._on_pause:
                self._on_pause()

    def resize(self, cols: int, rows: int) -> None:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)

    def on_readable(self) -> None:
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""

        if not data:
            # PTY likely closed
            self.loop.stop()
            return

        self._stdout_sink.write(data)
        for listener in self._output_listeners:
            try:
                listener(data)
            except Exception:
                pass

    def on_writable(self) -> None:
        if not self._wbuf:
            return

        try:
            n = os.write(self.fd, self._wbuf)
            if n:
                del self._wbuf[:n]
        except BlockingIOError, InterruptedError:
            pass
        except OSError as e:
            self.on_error(e)
            return

        # Update interests (will remove WRITE if buffer empty)
        self.loop.modify(self, self.interests())

        if self._paused and len(self._wbuf) <= LOW_WATER:
            self._paused = False
            if self._on_drain:
                self._on_drain()

    def on_error(self, exc: BaseException) -> None:
        self.loop.stop()

    def on_close(self) -> None:
        os.close(self.fd)
