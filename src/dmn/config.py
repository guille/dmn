import os

_RUNTIME_DIR: str = os.environ.get("XDG_RUNTIME_DIR", "/tmp")

DEFAULT_SESSION: str = "default"
DEFAULT_SHELL: str = "/bin/zsh"
LOG_PATH: str = os.path.join(_RUNTIME_DIR, "dmn.log")


def sock_path(session: str = DEFAULT_SESSION) -> str:
    return os.path.join(_RUNTIME_DIR, f"dmn-{session}.sock")
