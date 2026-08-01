#!/usr/bin/env python
"""
Attach client for dmn. Connects to the daemon's Unix socket,
sends ATTACH, then bridges the local terminal to the remote PTY.

Usage: dmn-attach [-s SESSION]
Detach: Ctrl+] or ~.
"""

import argparse
import fcntl
import json
import os
import select
import signal
import socket
import struct
import sys
import termios
import tty

_RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
DEFAULT_SESSION = "default"
DETACH_KEY = b"\x1d"  # Ctrl+]
ESCAPE_CHAR = ord(b"~")
DISCONNECT_CHAR = ord(b".")


def sock_path(session: str) -> str:
    return os.path.join(_RUNTIME_DIR, f"dmn-{session}.sock")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", "-s", default=DEFAULT_SESSION)
    args = ap.parse_args()

    path = sock_path(args.session)

    if not os.path.exists(path):
        print(f"Socket not found: {path}", file=sys.stderr)
        return 1

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(path)
    except ConnectionRefusedError:
        print("Connection refused (dmn not running?)", file=sys.stderr)
        return 1

    # Send ATTACH request
    attach_msg = json.dumps({"type": "ATTACH"}) + "\n"
    sock.sendall(attach_msg.encode())

    # Read the OK response (single line)
    resp_buf = b""
    while b"\n" not in resp_buf:
        chunk = sock.recv(4096)
        if not chunk:
            print("Connection closed before ATTACH response", file=sys.stderr)
            return 1
        resp_buf += chunk

    resp_line, remainder = resp_buf.split(b"\n", 1)
    try:
        resp = json.loads(resp_line)
        if not resp.get("ok"):
            print(f"ATTACH failed: {resp.get('error', 'unknown')}", file=sys.stderr)
            return 1
    except json.JSONDecodeError:
        print(f"Invalid response: {resp_line!r}", file=sys.stderr)
        return 1

    # If there's data after the response line, it's already PTY output
    initial_output = remainder

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()

    # Save terminal state and switch to raw mode
    old_attrs = termios.tcgetattr(stdin_fd)
    detached = False
    # State for ~. escape sequence: True when last byte sent was \n (or start of session)
    after_newline = True
    saw_escape = False
    sig_r = sig_w = -1
    old_sigwinch = signal.SIG_DFL

    try:
        tty.setraw(stdin_fd)
        sock.setblocking(False)
        os.set_blocking(stdin_fd, False)

        # SIGWINCH self-pipe for resize forwarding
        sig_r, sig_w = os.pipe()
        os.set_blocking(sig_r, False)
        os.set_blocking(sig_w, False)
        old_sigwinch = signal.signal(
            signal.SIGWINCH, lambda *_: os.write(sig_w, b"\x00")
        )

        def send_resize() -> None:
            try:
                ws = struct.pack("HHHH", 0, 0, 0, 0)
                result = fcntl.ioctl(stdout_fd, termios.TIOCGWINSZ, ws)
                rows, cols = struct.unpack("HHHH", result)[:2]
                sock.sendall(b"\x00R" + struct.pack("!HH", cols, rows))
            except OSError:
                pass

        # Send initial size
        send_resize()

        # Write any initial output
        if initial_output:
            os.write(stdout_fd, initial_output)

        while True:
            readable, _, _ = select.select([stdin_fd, sock, sig_r], [], [])

            for fd in readable:
                if fd == sig_r:
                    # Drain the pipe and send resize
                    try:
                        os.read(sig_r, 64)
                    except BlockingIOError:
                        pass
                    send_resize()

                elif fd is sock:
                    try:
                        data = sock.recv(65536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        data = b""
                    if not data:
                        return 0
                    os.write(stdout_fd, data)

                elif fd == stdin_fd:
                    try:
                        data = os.read(stdin_fd, 65536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        data = b""
                    if not data:
                        return 0

                    # Check for Ctrl+]
                    if DETACH_KEY in data:
                        detached = True
                        return 0

                    # Process ~. escape sequence byte-by-byte
                    out = bytearray()
                    for b in data:
                        if saw_escape:
                            saw_escape = False
                            if b == DISCONNECT_CHAR:
                                detached = True
                                return 0
                            if b == ESCAPE_CHAR:
                                # ~~ sends a literal ~
                                out.append(ESCAPE_CHAR)
                                continue
                            # Not a recognized escape — send the buffered ~ and this byte
                            out.append(ESCAPE_CHAR)
                            out.append(b)
                        elif after_newline and b == ESCAPE_CHAR:
                            saw_escape = True
                            continue
                        else:
                            out.append(b)

                        after_newline = b in (0x0A, 0x0D)  # \n or \r

                    if out:
                        try:
                            sock.sendall(bytes(out))
                        except OSError:
                            return 1

    except KeyboardInterrupt:
        return 0
    finally:
        signal.signal(signal.SIGWINCH, old_sigwinch)
        if sig_r >= 0:
            os.close(sig_r)
        if sig_w >= 0:
            os.close(sig_w)
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)
        os.set_blocking(stdin_fd, True)
        sock.close()
        if detached:
            print("\r\n[detached from dmn]")


if __name__ == "__main__":
    sys.exit(main())
