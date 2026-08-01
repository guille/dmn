# pyright: reportUnusedCallResult=false
import contextlib
import logging
import socket
from collections.abc import Callable
from typing import final

from dmn.protocols import EventLoopProtocol
from dmn.tasks import Interest, Task

log = logging.getLogger(__name__)


@final
class UnixServerTask:
    """Listen on a Unix socket and spawn a handler task for each incoming connection."""

    MAX_ERRORS = 5

    def __init__(
        self,
        sock: socket.socket,
        conn_task_factory: Callable[[socket.socket], Task],
        *,
        loop: EventLoopProtocol,
    ):
        self.loop = loop
        self.sock: socket.socket = sock
        self.sock.listen()
        self._conn_task_factory = conn_task_factory
        self._error_count = 0

    def fileno(self) -> int:
        return self.sock.fileno()

    def interests(self) -> int:
        return Interest.READ

    def on_readable(self) -> None:
        while True:
            try:
                c = self.sock.accept()[0]
                c.setblocking(False)
            except BlockingIOError:
                break
            # create & register per-connection task
            try:
                conn_task = self._conn_task_factory(c)
                self.loop.register(conn_task)
            except Exception:  # noqa: BLE001
                c.close()

            self._error_count = 0

    def on_writable(self) -> None:
        pass

    def on_error(self, exc: BaseException) -> None:
        self._error_count += 1
        log.error("accept error (%d/%d): %s", self._error_count, self.MAX_ERRORS, exc)
        if self._error_count >= self.MAX_ERRORS:
            log.error("max accept errors reached, stopping")
            self.loop.stop()

    def on_close(self) -> None:
        with contextlib.suppress(Exception):
            self.sock.close()
