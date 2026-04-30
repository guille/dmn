# pyright: basic

import os

from dmn.event_loop import EventLoop
from dmn.protocols import EventLoopProtocol
from dmn.tasks import Interest


class CollectorTask:
    """A simple task backed by a pipe fd that records callbacks."""

    def __init__(
        self, fd: int, *, loop: EventLoopProtocol, interest: int = Interest.READ
    ):
        self.fd = fd
        self.loop = loop
        self._interest = interest
        self.readable_count = 0
        self.writable_count = 0
        self.closed = False
        self.errors: list[BaseException] = []

    def fileno(self):
        return self.fd

    def interests(self):
        return self._interest

    def on_readable(self):
        self.readable_count += 1
        os.read(self.fd, 4096)

    def on_writable(self):
        self.writable_count += 1

    def on_error(self, exc):
        self.errors.append(exc)

    def on_close(self):
        self.closed = True


class StopAfterNReads:
    """Task that stops the loop after N readable events."""

    def __init__(self, fd: int, *, loop: EventLoopProtocol, stop_after: int = 1):
        self.fd = fd
        self.loop = loop
        self._stop_after = stop_after
        self._count = 0
        self.data = bytearray()
        self.closed = False

    def fileno(self):
        return self.fd

    def interests(self):
        return Interest.READ

    def on_readable(self):
        try:
            chunk = os.read(self.fd, 4096)
            self.data.extend(chunk)
        except OSError:
            pass
        self._count += 1
        if self._count >= self._stop_after:
            self.loop.stop()

    def on_writable(self):
        pass

    def on_error(self, exc):
        pass

    def on_close(self):
        self.closed = True


class TestEventLoop:
    def _make_loop(self):
        loop = EventLoop()
        return loop

    def test_register_and_run_readable(self):
        loop = self._make_loop()
        r, w = os.pipe()
        task = StopAfterNReads(r, loop=loop, stop_after=1)
        loop.register(task)
        os.write(w, b"hello")
        loop.run()
        assert task.data == bytearray(b"hello")
        os.close(r)
        os.close(w)

    def test_unregister_calls_on_close(self):
        loop = self._make_loop()
        r, w = os.pipe()
        task = CollectorTask(r, loop=loop)
        loop.register(task)
        loop.unregister(task)
        assert task.closed
        os.close(r)
        os.close(w)

    def test_stop_ends_loop(self):
        loop = self._make_loop()
        r, w = os.pipe()
        task = StopAfterNReads(r, loop=loop, stop_after=1)
        loop.register(task)
        os.write(w, b"x")
        loop.run()
        assert task.closed  # cleanup in finally
        os.close(r)
        os.close(w)

    def test_modify_changes_interest(self):
        loop = self._make_loop()
        r, w = os.pipe()
        task = CollectorTask(r, loop=loop, interest=0)  # start with no interest
        loop.register(task)

        # Modify to READ
        loop.modify(task, Interest.READ)
        os.write(w, b"data")

        # Use a second pipe to stop the loop
        r2, w2 = os.pipe()
        stop_task = StopAfterNReads(r2, loop=loop, stop_after=1)
        loop.register(stop_task)
        os.write(w2, b"x")
        loop.run()

        # The collector should have been triggered
        assert task.readable_count >= 1
        os.close(r)
        os.close(w)
        os.close(r2)
        os.close(w2)

    def test_data_flow_through_pipe_tasks(self):
        """Integration: stdin_task -> pty_task -> stdout_task using real pipes."""
        from dmn.tasks.pty_task import PTYTask
        from dmn.tasks.stdout_write_task import StdoutWriteTask

        # Create pipe pairs to simulate pty and stdout
        pty_r, pty_w = os.pipe()
        stdout_r, stdout_w = os.pipe()

        os.set_blocking(pty_r, False)
        os.set_blocking(pty_w, False)
        os.set_blocking(stdout_r, False)
        os.set_blocking(stdout_w, False)

        loop = EventLoop()

        # StdoutWriteTask writes to stdout_w; we read from stdout_r
        stdout_task = StdoutWriteTask.__new__(StdoutWriteTask)
        stdout_task.loop = loop
        stdout_task.fd = stdout_w
        stdout_task._wbuf = bytearray()

        pty_task = PTYTask(pty_r, sink=stdout_task, loop=loop)

        loop.register(stdout_task)
        loop.register(pty_task)

        # Write data to pty pipe (simulating shell output)
        os.write(pty_w, b"shell output")
        os.close(pty_w)  # EOF after data

        loop.run()

        # Read what came out of stdout
        os.set_blocking(stdout_r, False)
        try:
            result = os.read(stdout_r, 4096)
        except BlockingIOError:
            result = b""

        # stdout_task may have data still in wbuf if it didn't flush
        remaining = bytes(stdout_task._wbuf)
        total = result + remaining

        assert total == b"shell output"

        os.close(stdout_r)
        os.close(stdout_w)
