from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from dmn.tasks import Sink


@final
@dataclass(frozen=True, slots=True)
class HandlerContext:
    """
    Shared context passed to handlers.
    """

    pty_sink: Sink


@runtime_checkable
class CommandHandler(Protocol):
    def handle(
        self, payload: dict[str, Any], ctx: HandlerContext
    ) -> dict[str, Any]: ...


class HandlerError(Exception):
    """Raised by handlers for client-facing errors (invalid payload, missing fields, etc.)"""

    pass
