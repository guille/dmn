# pyright: basic

import json
import socket
import struct
from unittest.mock import MagicMock

from dmn.api.dispatcher import CommandDispatcher
from dmn.api.handlers.base import HandlerContext
from dmn.api.handlers.exec import ExecHandler
from dmn.tasks.handle_sock_connection_task import HandleSockConnectionTask


class FakeSink:
    def __init__(self):
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def set_flow_control(self, *, on_pause, on_drain):
        pass


class FakeLoop:
    def __init__(self):
        self.unregistered: list = []
        self.modifications: list = []

    def unregister(self, task):
        self.unregistered.append(task)
        task.on_close()

    def modify(self, task, interests):
        self.modifications.append((task, interests))


def _make_task():
    c, s = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    c.setblocking(False)
    s.setblocking(False)
    sink = FakeSink()
    ctx = HandlerContext(pty_sink=sink)
    handlers: dict[str, ExecHandler] = {"EXEC": ExecHandler()}
    dispatcher = CommandDispatcher(handlers=handlers, ctx=ctx)  # pyright: ignore[reportArgumentType]
    loop = FakeLoop()
    pty_task = MagicMock()
    task = HandleSockConnectionTask(c, dispatcher, pty_task, loop=loop)  # pyright: ignore[reportArgumentType]
    return task, c, s, sink, loop, pty_task


class TestHandleSockConnectionTask:
    def test_exec_command_reaches_sink(self):
        task, client_sock, server_end, sink, _, _ = _make_task()
        msg = json.dumps({"type": "EXEC", "payload": {"command": "ls\n"}}) + "\n"
        server_end.sendall(msg.encode())
        task.on_readable()
        assert sink.written == [b"ls\n"]
        client_sock.close()
        server_end.close()

    def test_invalid_json_returns_error(self):
        task, client_sock, server_end, _, _, _ = _make_task()
        server_end.sendall(b"not json\n")
        task.on_readable()
        # Response should be in wbuf
        resp = json.loads(task.wbuf.decode())
        assert resp["ok"] is False
        assert "invalid json" in resp["error"]
        client_sock.close()
        server_end.close()

    def test_unknown_type_returns_error(self):
        task, client_sock, server_end, _, _, _ = _make_task()
        msg = json.dumps({"type": "UNKNOWN", "payload": {}}) + "\n"
        server_end.sendall(msg.encode())
        task.on_readable()
        resp = json.loads(task.wbuf.decode())
        assert resp["ok"] is False
        assert "unsupported" in resp["error"]
        client_sock.close()
        server_end.close()

    def test_multiple_lines_in_one_read(self):
        task, client_sock, server_end, sink, _, _ = _make_task()
        msg1 = json.dumps({"type": "EXEC", "payload": {"command": "a\n"}}) + "\n"
        msg2 = json.dumps({"type": "EXEC", "payload": {"command": "b\n"}}) + "\n"
        server_end.sendall((msg1 + msg2).encode())
        task.on_readable()
        assert sink.written == [b"a\n", b"b\n"]
        client_sock.close()
        server_end.close()

    def test_eof_unregisters(self):
        task, client_sock, server_end, _, loop, _ = _make_task()
        server_end.close()
        task.on_readable()
        assert task in loop.unregistered
        client_sock.close()

    def test_response_end_with_newline(self):
        task, client_sock, server_end, _, _, _ = _make_task()
        msg = json.dumps({"type": "EXEC", "payload": {"command": "x"}}) + "\n"
        server_end.sendall(msg.encode())
        task.on_readable()
        raw = task.wbuf.decode()
        assert raw.endswith("\n")
        client_sock.close()
        server_end.close()

    def test_writable_flushes_wbuf(self):
        task, client_sock, server_end, _, _, _ = _make_task()
        task.wbuf.extend(b'{"ok": true}\n')
        task.on_writable()
        received = server_end.recv(4096)
        assert received == b'{"ok": true}\n'
        client_sock.close()
        server_end.close()

    def test_attach_then_data_forwarded_to_pty(self):
        task, client_sock, server_end, _, _, pty = _make_task()
        msg = json.dumps({"type": "ATTACH"}) + "\n"
        server_end.sendall(msg.encode())
        task.on_readable()
        # Verify attach response
        resp = json.loads(task.wbuf.split(b"\n")[0])
        assert resp["ok"] is True
        # Send data after attach
        task.wbuf.clear()
        server_end.sendall(b"hello")
        task.on_readable()
        pty.write.assert_called_with(b"hello")
        client_sock.close()
        server_end.close()

    def test_attach_resize_parsed(self):
        task, client_sock, server_end, _, _, pty = _make_task()
        msg = json.dumps({"type": "ATTACH"}) + "\n"
        server_end.sendall(msg.encode())
        task.on_readable()
        task.wbuf.clear()
        # Send resize: \x00R + cols(100) + rows(50)
        resize = b"\x00R" + struct.pack("!HH", 100, 50)
        server_end.sendall(resize)
        task.on_readable()
        pty.resize.assert_called_with(100, 50)
        pty.write.assert_not_called()
        client_sock.close()
        server_end.close()

    def test_attach_resize_mixed_with_data(self):
        task, client_sock, server_end, _, _, pty = _make_task()
        msg = json.dumps({"type": "ATTACH"}) + "\n"
        server_end.sendall(msg.encode())
        task.on_readable()
        task.wbuf.clear()
        # Data before resize, data after resize
        resize = b"\x00R" + struct.pack("!HH", 80, 24)
        payload = b"before" + resize + b"after"
        server_end.sendall(payload)
        task.on_readable()
        pty.resize.assert_called_with(80, 24)
        calls = [c.args[0] for c in pty.write.call_args_list]
        assert b"before" in calls
        assert b"after" in calls
        client_sock.close()
        server_end.close()

    def test_attach_split_resize_across_reads(self):
        task, client_sock, server_end, _, _, pty = _make_task()
        msg = json.dumps({"type": "ATTACH"}) + "\n"
        server_end.sendall(msg.encode())
        task.on_readable()
        task.wbuf.clear()
        # Send \x00 at end of first read
        server_end.sendall(b"x\x00")
        task.on_readable()
        pty.write.assert_called_with(b"x")
        pty.reset_mock()
        # Send R + size in second read
        server_end.sendall(b"R" + struct.pack("!HH", 120, 40))
        task.on_readable()
        pty.resize.assert_called_with(120, 40)
        client_sock.close()
        server_end.close()

    def test_attach_nul_not_resize(self):
        """A NUL followed by non-R should be forwarded as data."""
        task, client_sock, server_end, _, _, pty = _make_task()
        msg = json.dumps({"type": "ATTACH"}) + "\n"
        server_end.sendall(msg.encode())
        task.on_readable()
        task.wbuf.clear()
        server_end.sendall(b"\x00X")
        task.on_readable()
        calls = [c.args[0] for c in pty.write.call_args_list]
        combined = b"".join(calls)
        assert b"\x00X" == combined
        client_sock.close()
        server_end.close()

    def test_watch_receives_output_but_input_discarded(self):
        task, client_sock, server_end, _, _, pty = _make_task()
        msg = json.dumps({"type": "WATCH"}) + "\n"
        server_end.sendall(msg.encode())
        task.on_readable()
        # Verify watch response
        resp = json.loads(task.wbuf.split(b"\n")[0])
        assert resp["ok"] is True
        assert resp.get("data", {}).get("mode") == "watch"
        # Verify output listener was added
        pty.add_output_listener.assert_called_once()
        # Send input — should be discarded
        task.wbuf.clear()
        pty.reset_mock()
        server_end.sendall(b"typing")
        task.on_readable()
        pty.write.assert_not_called()
        client_sock.close()
        server_end.close()

    def test_watch_cleanup_removes_listener(self):
        task, client_sock, server_end, _, loop, pty = _make_task()
        msg = json.dumps({"type": "WATCH"}) + "\n"
        server_end.sendall(msg.encode())
        task.on_readable()
        # Close connection
        server_end.close()
        task.on_readable()
        # Should have unregistered and removed listener
        assert task in loop.unregistered
        pty.remove_output_listener.assert_called_once()
        client_sock.close()
