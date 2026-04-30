from typing import Any, override

from dmn.api.handlers.base import CommandHandler, HandlerContext, HandlerError
from dmn.exceptions import BufferFullError


class ExecHandler(CommandHandler):
    """
    Payload: {"command": "ls -la\n"}
    Writes text to PTY sink's buffer as bytes.
    Returns: {}
    """

    @override
    def handle(self, payload: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        command = payload.get("command")
        if not isinstance(command, str):
            raise HandlerError("payload.command (str) is required")
        try:
            ctx.pty_sink.write(command.encode())
        except BufferFullError:
            raise HandlerError("PTY buffer full, try again later")

        return {}
