import fcntl
import os
import pty
import sys
import termios
from typing import NoReturn

# pyright: reportUnusedCallResult=false


class PTYSession:
    @classmethod
    def spawn(cls, shell: str) -> tuple[int, int] | NoReturn:
        master_fd, slave_fd = pty.openpty()
        fcntl.fcntl(master_fd, fcntl.F_SETFL, os.O_NONBLOCK)

        pid = os.fork()
        if pid == 0:  # child
            # become the session leader
            os.setsid()

            # make PTY slave the controlling TTY
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY)

            # attach slave pty as stdio
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.close(master_fd)

            # make the shell the foreground process group
            os.tcsetpgrp(0, os.getpid())

            os.environ["PTY_BRIDGE"] = "1"
            try:
                os.execvp(shell, [shell])
            except Exception as e:
                print("exec failed:", e, file=sys.stderr)
                os._exit(1)
        else:  # parent
            # we don't need the slave fd
            os.close(slave_fd)

            return master_fd, pid
