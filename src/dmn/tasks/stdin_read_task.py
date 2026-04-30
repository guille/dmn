import os
from typing import final

from dmn.protocols import EventLoopProtocol
from dmn.tasks import Interest, Sink


@final
class StdinReadTask:
    """
    When stdin is ready for reading, read data from it and enqueue for the pty (via sink)
    """

    def __init__(self, stdin_fd: int, *, sink: Sink, loop: EventLoopProtocol):
        self.loop = loop
        self.fd: int = stdin_fd
        self.sink = sink
        self._read_paused = False

        sink.set_flow_control(
            on_pause=self._pause_reading,
            on_drain=self._resume_reading,
        )

    def _pause_reading(self) -> None:
        self._read_paused = True
        self.loop.modify(self, 0)

    def _resume_reading(self) -> None:
        self._read_paused = False
        self.loop.modify(self, Interest.READ)

    def fileno(self) -> int:
        return self.fd

    def interests(self) -> int:
        return 0 if self._read_paused else Interest.READ

    def on_readable(self):
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            # stdin closed
            self.loop.stop()
            return

        self.sink.write(data)

    def on_writable(self):
        pass

    def on_error(self, exc: BaseException):
        self.loop.stop()

    def on_close(self):
        pass
