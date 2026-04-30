import os
import sys
from collections.abc import Callable
from typing import final

from dmn.protocols import EventLoopProtocol
from dmn.tasks import Interest

HIGH_WATER = 256 * 1024  # 256 KB
LOW_WATER = 64 * 1024  # 64 KB


@final
class StdoutWriteTask:
    """
    Manages non-blocking writes to stdout.
    """

    def __init__(self, *, loop: EventLoopProtocol):
        self.loop = loop
        self.fd: int = sys.stdout.fileno()
        self._wbuf = bytearray()
        self._paused = False
        self._on_pause: Callable[[], None] | None = None
        self._on_drain: Callable[[], None] | None = None

    def fileno(self) -> int:
        return self.fd

    def interests(self) -> int:
        # Only interested in WRITE if we have data
        return Interest.WRITE if self._wbuf else 0

    def set_flow_control(
        self, *, on_pause: Callable[[], None], on_drain: Callable[[], None]
    ) -> None:
        self._on_pause = on_pause
        self._on_drain = on_drain

    def write(self, data: bytes) -> None:
        if not data:
            return
        self._wbuf.extend(data)
        self.loop.modify(self, self.interests())

        if not self._paused and len(self._wbuf) > HIGH_WATER:
            self._paused = True
            if self._on_pause:
                self._on_pause()

    def on_readable(self) -> None:
        pass  # Stdout is write-only for us here

    def on_writable(self) -> None:
        try:
            if self._wbuf:
                n = os.write(self.fd, self._wbuf)
                if n:
                    del self._wbuf[:n]
        except BlockingIOError, InterruptedError:
            pass
        except OSError as e:
            self.on_error(e)
            return

        # Ensure our interests are up to date (WRITE if buffer, else 0)
        self.loop.modify(self, self.interests())

        if self._paused and len(self._wbuf) <= LOW_WATER:
            self._paused = False
            if self._on_drain:
                self._on_drain()

    def on_error(self, exc: BaseException) -> None:
        self.loop.stop()

    def on_close(self) -> None:
        pass
