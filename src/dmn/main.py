# pyright: reportUnusedCallResult=false

import argparse
import errno
import logging
import logging.handlers
import os
import signal
import socket
import sys

from dmn.api.dispatcher import CommandDispatcher
from dmn.api.handlers.base import CommandHandler, HandlerContext
from dmn.api.handlers.exec import ExecHandler
from dmn.event_loop import EventLoop
from dmn.pty_session import PTYSession
from dmn.tasks.handle_sigwinch_task import HandleSigwinchTask
from dmn.tasks.handle_sock_connection_task import HandleSockConnectionTask
from dmn.tasks.pty_task import PTYTask
from dmn.tasks.stdin_read_task import StdinReadTask
from dmn.tasks.stdout_write_task import StdoutWriteTask
from dmn.tasks.unix_server_task import UnixServerTask
from dmn.terminal_bridge import TerminalBridge

from .config import DEFAULT_SESSION, DEFAULT_SHELL, LOG_PATH, sock_path

log = logging.getLogger("dmn")


def _setup_logging(session: str) -> None:
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=0
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(session)s] %(name)s: %(message)s"
        )
    )
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    # Inject session into all log records from this logger
    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.session = session  # pyright: ignore[reportAttributeAccessIssue]
        return record

    logging.setLogRecordFactory(factory)


def bind_socket(path: str) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(path)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise

        try:
            sock.connect(path)
        except ConnectionRefusedError:
            # Stale socket: try to unlink and re-bind
            sock.close()
            os.unlink(path)
            log.info("removed stale socket at %s", path)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(path)
        except OSError:
            sock.close()
            raise
        else:
            sock.close()
            raise Exception("dmn already running in a terminal; exiting.")

    return sock


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shell", default=os.environ.get("SHELL", DEFAULT_SHELL))
    ap.add_argument("--session", "-s", default=DEFAULT_SESSION)
    args = ap.parse_args()

    session: str = args.session
    path = sock_path(session)

    _setup_logging(session)

    try:
        sock = bind_socket(path)
        sock.setblocking(False)
        os.chmod(path, 0o660)
    except Exception as e:
        print(e)
        return 2

    master_fd, shell_pid = PTYSession.spawn(args.shell)
    log.info("started pid=%d shell=%s socket=%s", shell_pid, args.shell, path)

    try:
        with TerminalBridge(sys.stdin, master_fd) as bridge:
            loop = EventLoop()

            stdout_write_task = StdoutWriteTask(loop=loop)
            pty_task = PTYTask(master_fd, sink=stdout_write_task, loop=loop)

            stdout_write_task.set_flow_control(
                on_pause=pty_task.pause_reading,
                on_drain=pty_task.resume_reading,
            )

            stdin_read_task = StdinReadTask(
                sys.stdin.fileno(), sink=pty_task, loop=loop
            )
            handle_sigwinch_task = HandleSigwinchTask(bridge, loop=loop)

            handlers: dict[str, CommandHandler] = {
                "EXEC": ExecHandler(),
            }
            ctx = HandlerContext(pty_sink=pty_task)
            command_dispatcher = CommandDispatcher(handlers=handlers, ctx=ctx)

            # create factory closure that injects pty sink
            def sock_conn_factory(s: socket.socket):
                return HandleSockConnectionTask(
                    s, command_dispatcher, pty_task, loop=loop
                )

            unix_server_task = UnixServerTask(sock, sock_conn_factory, loop=loop)

            loop.register(stdout_write_task)
            loop.register(pty_task)
            loop.register(stdin_read_task)
            loop.register(handle_sigwinch_task)
            loop.register(unix_server_task)

            print(f"PTY bridge running. Unix socket at: {path}\n")
            sys.stdout.flush()

            loop.run()
        return 0
    finally:  # CLEANUP
        log.info("shutting down")
        try:
            os.unlink(path)
        except Exception:
            pass
        try:
            os.kill(shell_pid, signal.SIGHUP)
            os.waitpid(shell_pid, 0)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
