# pyright: reportUnusedCallResult=false

import errno
import logging
import os
import selectors
import time
from typing import TYPE_CHECKING, cast, final

if TYPE_CHECKING:
    from dmn.protocols import Task

log = logging.getLogger(__name__)


@final
class EventLoop:
    def __init__(self):
        self._selector = selectors.DefaultSelector()
        self.tasks: dict[int, Task] = {}
        self._stop = False

    def register(self, task: "Task") -> None:
        fd = task.fileno()
        if fd in self.tasks:
            return

        os.set_blocking(fd, False)
        interests = task.interests()
        if interests:
            self._selector.register(fd, interests, task)
        self.tasks[fd] = task

    def unregister(self, task: "Task") -> None:
        fd = task.fileno()
        try:
            self._selector.unregister(fd)
        except Exception:
            pass
        try:
            task.on_close()
        except Exception:
            pass

        if fd in self.tasks:
            del self.tasks[fd]

    def modify(self, task: "Task", interests: int) -> None:
        fd = task.fileno()

        try:
            self._selector.get_key(fd)
            if interests:
                self._selector.modify(fd, interests, task)
            else:
                self._selector.unregister(fd)
        except KeyError:
            if interests:
                self._selector.register(fd, interests, task)

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            while not self._stop:
                self._poll_once()
            self._drain()
        except KeyboardInterrupt:
            pass
        finally:
            self._cleanup()

    def _poll_once(
        self, *, writes_only: bool = False, timeout: float | None = None
    ) -> None:
        for key, mask in self._select(timeout):
            if writes_only:
                mask &= selectors.EVENT_WRITE
            if mask:
                self._dispatch(key, mask)

    def _select(
        self, timeout: float | None = None
    ) -> list[tuple[selectors.SelectorKey, int]]:
        try:
            return self._selector.select(timeout)
        except OSError as e:
            if e.errno == errno.EINTR:
                return []
            raise

    def _dispatch(self, key: selectors.SelectorKey, mask: int) -> None:
        task = cast("Task", key.data)
        try:
            if mask & selectors.EVENT_READ:
                task.on_readable()
            if mask & selectors.EVENT_WRITE:
                task.on_writable()
        except Exception as exc:
            log.warning("task %s error: %s", task.__class__.__name__, exc)
            try:
                task.on_error(exc)
            except Exception:
                pass

    def _drain(self):
        deadline = time.monotonic() + 0.2  # 200ms max
        while time.monotonic() < deadline:
            if not any(
                t.interests() & selectors.EVENT_WRITE for t in self.tasks.values()
            ):
                break
            self._poll_once(writes_only=True, timeout=0.05)

    def _cleanup(self) -> None:
        for task in list(self.tasks.values()):
            try:
                self.unregister(task)
            except Exception:
                pass
        self._selector.close()
