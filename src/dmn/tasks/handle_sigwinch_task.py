from typing import final

from dmn.protocols import EventLoopProtocol
from dmn.tasks import Interest
from dmn.terminal_bridge import TerminalBridge


@final
class HandleSigwinchTask:
    """
    When SIGWINCH is caught, call the handler
    """

    def __init__(self, terminal_bridge: TerminalBridge, loop: EventLoopProtocol):
        self.loop = loop
        self._terminal_bridge = terminal_bridge

    def fileno(self) -> int:
        return self._terminal_bridge.sigwinch_fd

    def interests(self) -> int:
        return Interest.READ

    def on_readable(self):
        try:
            self._terminal_bridge.handle_winch()
        except Exception:
            pass

    def on_writable(self):
        pass

    def on_error(self, exc: BaseException):
        # cosmetic only, non-fatal
        pass

    def on_close(self):
        # No cleanup, terminal bridge's contextmanager handles restoring signal handler
        pass
