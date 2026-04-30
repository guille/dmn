# pyright: basic

import os
from unittest.mock import MagicMock

import pytest

from dmn.exceptions import BufferFullError
from dmn.tasks import Interest
from dmn.tasks.pty_task import HIGH_WATER as PTY_HIGH_WATER
from dmn.tasks.pty_task import LOW_WATER as PTY_LOW_WATER
from dmn.tasks.pty_task import MAX_BUFFER as PTY_MAX_BUFFER
from dmn.tasks.pty_task import PTYTask
from dmn.tasks.stdin_read_task import StdinReadTask
from dmn.tasks.stdout_write_task import HIGH_WATER as STDOUT_HIGH_WATER
from dmn.tasks.stdout_write_task import LOW_WATER as STDOUT_LOW_WATER
from dmn.tasks.stdout_write_task import StdoutWriteTask


class FakeLoop:
    def __init__(self):
        self.modifications: list = []

    def register(self, task):
        pass

    def unregister(self, task):
        pass

    def modify(self, task, interests):
        self.modifications.append((task, interests))

    def stop(self):
        pass


class FakeSink:
    def __init__(self):
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def set_flow_control(self, *, on_pause, on_drain):
        pass


class TestStdoutWriteTaskBackpressure:
    def _make(self):
        loop = FakeLoop()
        task = StdoutWriteTask(loop=loop)
        on_pause = MagicMock()
        on_drain = MagicMock()
        task.set_flow_control(on_pause=on_pause, on_drain=on_drain)
        return task, loop, on_pause, on_drain

    def test_no_pause_under_high_water(self):
        task, _, on_pause, _ = self._make()
        task.write(b"x" * (STDOUT_HIGH_WATER - 1))
        on_pause.assert_not_called()

    def test_pause_over_high_water(self):
        task, _, on_pause, _ = self._make()
        task.write(b"x" * (STDOUT_HIGH_WATER + 1))
        on_pause.assert_called_once()

    def test_pause_called_once(self):
        task, _, on_pause, _ = self._make()
        task.write(b"x" * (STDOUT_HIGH_WATER + 1))
        task.write(b"x" * 100)
        on_pause.assert_called_once()

    def test_drain_after_on_writable(self):
        """Write enough to pause, then simulate on_writable draining below LOW_WATER."""
        r, w = os.pipe()
        os.set_blocking(w, False)
        os.set_blocking(r, False)
        try:
            task, _, on_pause, on_drain = self._make()
            task.fd = w  # redirect writes to pipe

            task.write(b"x" * (STDOUT_HIGH_WATER + 1))
            on_pause.assert_called_once()

            # Drain by calling on_writable repeatedly until below low water
            while len(task._wbuf) > STDOUT_LOW_WATER:
                try:
                    os.read(r, 65536)
                except BlockingIOError:
                    pass
                task.on_writable()

            on_drain.assert_called_once()
        finally:
            os.close(r)
            os.close(w)


class TestPTYTaskBackpressure:
    def _make(self):
        r, w = os.pipe()
        os.set_blocking(w, False)
        os.set_blocking(r, False)
        loop = FakeLoop()
        sink = FakeSink()
        task = PTYTask(w, sink=sink, loop=loop)
        on_pause = MagicMock()
        on_drain = MagicMock()
        task.set_flow_control(on_pause=on_pause, on_drain=on_drain)
        return task, loop, on_pause, on_drain, r, w

    def test_pause_over_high_water(self):
        task, _, on_pause, _, r, w = self._make()
        try:
            task.write(b"x" * (PTY_HIGH_WATER + 1))
            on_pause.assert_called_once()
        finally:
            os.close(r)
            os.close(w)

    def test_drain_after_on_writable(self):
        task, _, _, on_drain, r, w = self._make()
        try:
            task.write(b"x" * (PTY_HIGH_WATER + 1))

            while len(task._wbuf) > PTY_LOW_WATER:
                try:
                    os.read(r, 65536)
                except BlockingIOError:
                    pass
                task.on_writable()

            on_drain.assert_called_once()
        finally:
            os.close(r)
            os.close(w)

    def test_pause_reading(self):
        task, _, _, _, r, w = self._make()
        try:
            assert task.interests() & Interest.READ
            task.pause_reading()
            assert not (task.interests() & Interest.READ)
            task.resume_reading()
            assert task.interests() & Interest.READ
        finally:
            os.close(r)
            os.close(w)

    def test_write_raises_buffer_full_error(self):
        task, _, _, _, r, w = self._make()
        try:
            task.write(b"x" * (PTY_MAX_BUFFER - 100))
            with pytest.raises(BufferFullError):
                task.write(b"x" * 200)
        finally:
            os.close(r)
            os.close(w)

    def test_write_accepts_up_to_max_buffer(self):
        task, _, _, _, r, w = self._make()
        try:
            task.write(b"x" * PTY_MAX_BUFFER)
            assert len(task._wbuf) == PTY_MAX_BUFFER
        finally:
            os.close(r)
            os.close(w)


class TestStdinReadTaskBackpressure:
    def test_flow_control_wired_on_init(self):
        """StdinReadTask should register flow control callbacks on its sink."""
        r, _ = os.pipe()
        loop = FakeLoop()
        sink = MagicMock()
        sink.set_flow_control = MagicMock()

        StdinReadTask(r, sink=sink, loop=loop)

        sink.set_flow_control.assert_called_once()
        kwargs = sink.set_flow_control.call_args.kwargs
        assert "on_pause" in kwargs
        assert "on_drain" in kwargs
        os.close(r)
        os.close(_)

    def test_pause_removes_read_interest(self):
        r, w = os.pipe()
        loop = FakeLoop()
        sink = MagicMock()

        captured = {}

        def fake_set_flow_control(*, on_pause, on_drain):
            captured["on_pause"] = on_pause
            captured["on_drain"] = on_drain

        sink.set_flow_control = fake_set_flow_control

        task = StdinReadTask(r, sink=sink, loop=loop)
        assert task.interests() == Interest.READ

        captured["on_pause"]()
        assert task.interests() == 0

        captured["on_drain"]()
        assert task.interests() == Interest.READ

        os.close(r)
        os.close(w)
