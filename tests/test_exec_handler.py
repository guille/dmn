# pyright: basic

from dmn.api.handlers.base import HandlerContext, HandlerError
from dmn.api.handlers.exec import ExecHandler
from dmn.exceptions import BufferFullError


class FakeSink:
    def __init__(self):
        self.written: list[bytes] = []
        self._should_raise = False

    def write(self, data: bytes) -> None:
        if self._should_raise:
            raise BufferFullError("buffer full")
        self.written.append(data)

    def set_flow_control(self, *, on_pause, on_drain):
        pass


class TestExecHandler:
    def _make(self):
        sink = FakeSink()
        ctx = HandlerContext(pty_sink=sink)
        handler = ExecHandler()
        return handler, ctx, sink

    def test_writes_command_to_sink(self):
        handler, ctx, sink = self._make()
        result = handler.handle({"command": "ls -la\n"}, ctx)
        assert result == {}
        assert sink.written == [b"ls -la\n"]

    def test_missing_command_raises(self):
        handler, ctx, _ = self._make()
        try:
            handler.handle({}, ctx)
            assert False, "should have raised"
        except HandlerError as e:
            assert "command" in str(e)

    def test_non_string_command_raises(self):
        handler, ctx, _ = self._make()
        try:
            handler.handle({"command": 123}, ctx)
            assert False, "should have raised"
        except HandlerError:
            pass

    def test_buffer_full_raises_handler_error(self):
        handler, ctx, sink = self._make()
        sink._should_raise = True
        try:
            handler.handle({"command": "ls\n"}, ctx)
            assert False, "should have raised"
        except HandlerError as e:
            assert "buffer full" in str(e).lower()
