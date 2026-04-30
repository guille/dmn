#!/usr/bin/env python
import argparse
import json
import os
import socket

_RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
DEFAULT_SESSION = "default"


def sock_path(session: str) -> str:
    return os.path.join(_RUNTIME_DIR, f"dmn-{session}.sock")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", "-s", default=DEFAULT_SESSION)
    args = ap.parse_args()

    path = sock_path(args.session)

    if not os.path.exists(path):
        print(f"socket not found: {path}")
        return

    print("(Ctrl+D to exit)")

    while True:
        try:
            line = input("cmd> ")
        except EOFError:
            break
        if not line.strip():
            continue

        payload = (
            json.dumps(
                {
                    "type": "EXEC",
                    "payload": {"command": f"{line}\n"},
                }
            )
            + "\n"
        )

        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(path)
            s.sendall(payload.encode())
            s.close()
        except Exception as e:
            print("error:", e)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
