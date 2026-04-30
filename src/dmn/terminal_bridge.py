import contextlib
import fcntl
import os
import signal
import struct
import termios
import tty
from types import TracebackType
from typing import TYPE_CHECKING, TextIO, cast, final, override

if TYPE_CHECKING:
    from _typeshed import FileDescriptorLike

# pyright: reportUnusedCallResult=false

# ioctl constants for winsize
TIOCSWINSZ = getattr(termios, "TIOCSWINSZ", 0x5414)
TIOCGWINSZ = getattr(termios, "TIOCGWINSZ", 0x5413)


@final
class TerminalBridge(contextlib.AbstractContextManager["TerminalBridge"]):
    def __init__(self, stdin: TextIO, pty_master_fd: int):
        self.stdin = stdin
        self.pty_fd = pty_master_fd
        self._orig_attrs = []
        self._pipe_r: int | None = None
        self._pipe_w: int | None = None
        self._old_handler = None

    @property
    def sigwinch_fd(self) -> int:
        assert self._pipe_r is not None, "TerminalBridge not entered"
        return self._pipe_r

    def handle_winch(self):
        try:
            assert self._pipe_r is not None, "TerminalBridge not entered"
            # drain pipe
            while os.read(self._pipe_r, 1024):
                pass
        except OSError:
            pass

        self._adjust_winsize()

    @override
    def __enter__(self):  # pyright: ignore[reportMissingSuperCall]
        self._orig_attrs = termios.tcgetattr(self.stdin)
        tty.setraw(self.stdin.fileno())

        # propagate initial window size
        self._adjust_winsize()

        # self-pipe for signal handling
        self._pipe_r, self._pipe_w = os.pipe()
        os.set_blocking(self._pipe_r, False)
        os.set_blocking(self._pipe_w, False)

        self._old_handler = signal.getsignal(signal.SIGWINCH)
        signal.set_wakeup_fd(self._pipe_w)
        signal.signal(signal.SIGWINCH, lambda signum, frame: None)

        return self

    @override
    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ):
        # restore signal handler
        try:
            signal.signal(signal.SIGWINCH, self._old_handler)
        except Exception:
            pass

        # restore terminal attrs
        try:
            termios.tcsetattr(self.stdin, termios.TCSANOW, self._orig_attrs)
        except Exception:
            pass

        # close pipe
        for fd in (self._pipe_r, self._pipe_w):
            try:
                if fd is not None:
                    os.close(fd)
            except Exception:
                pass
        try:
            signal.set_wakeup_fd(-1)
        except Exception:
            pass

    def _adjust_winsize(self) -> None:
        try:
            rows, cols = self._get_winsize(self.stdin.fileno())
            self._set_winsize(self.pty_fd, rows, cols)
        except Exception:
            pass

    def _set_winsize(
        self, fd: FileDescriptorLike, rows: int, cols: int, xpix: int = 0, ypix: int = 0
    ) -> None:
        winsz = struct.pack("HHHH", rows, cols, xpix, ypix)
        fcntl.ioctl(fd, TIOCSWINSZ, winsz)

    def _get_winsize(self, fd: FileDescriptorLike) -> tuple[int, int]:
        data = fcntl.ioctl(fd, TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        rows, cols = cast(tuple[int, int, int, int], struct.unpack("HHHH", data))[:2]
        return rows, cols
