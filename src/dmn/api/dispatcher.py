import logging
from dataclasses import dataclass

from dmn.api.handlers.base import CommandHandler, HandlerContext, HandlerError
from dmn.api.protocol import Request, Response

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CommandDispatcher:
    handlers: dict[str, CommandHandler]
    ctx: HandlerContext

    def dispatch(self, req: Request) -> Response:
        handler = self.handlers.get(req.type)
        if handler is None:
            return Response(ok=False, error=f"unsupported type: {req.type}")

        try:
            data = handler.handle(req.payload, self.ctx)
            return Response(ok=True, data=data)
        except HandlerError as e:
            return Response(ok=False, error=str(e))
        except Exception:
            log.exception("handler %s raised unexpectedly", req.type)
            return Response(ok=False, error="internal error")
