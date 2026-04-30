# pyright: basic

from typing import Any

from dmn.api.dispatcher import CommandDispatcher
from dmn.api.handlers.base import CommandHandler, HandlerContext
from dmn.api.protocol import Request


class FakeSink:
    def __init__(self):
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def set_flow_control(self, *, on_pause, on_drain):
        pass


class EchoHandler:
    def handle(self, payload: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        return {"echo": payload}


class BrokenHandler:
    def handle(self, payload: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        raise RuntimeError("boom")


class TestCommandDispatcher:
    def _make_dispatcher(
        self, handlers: dict[str, CommandHandler]
    ) -> CommandDispatcher:
        sink = FakeSink()
        ctx = HandlerContext(pty_sink=sink)
        return CommandDispatcher(handlers=handlers, ctx=ctx)

    def test_dispatch_to_registered_handler(self):
        d = self._make_dispatcher({"EXEC": EchoHandler()})
        req = Request(type="EXEC", payload={"command": "ls"})
        resp = d.dispatch(req)
        assert resp.ok is True
        assert resp.data == {"echo": {"command": "ls"}}

    def test_dispatch_unknown_type(self):
        d = self._make_dispatcher({"EXEC": EchoHandler()})
        req = Request(type="QUERY", payload={})
        resp = d.dispatch(req)
        assert resp.ok is False
        assert resp.error is not None
        assert "unsupported type" in resp.error

    def test_dispatch_handler_exception(self):
        d = self._make_dispatcher({"EXEC": BrokenHandler()})
        req = Request(type="EXEC", payload={})
        resp = d.dispatch(req)
        assert resp.ok is False
        assert resp.error == "internal error"
