from dataclasses import dataclass
from typing import Any, TypedDict


@dataclass(frozen=True, slots=True)
class Request:
    type: str
    payload: dict[str, Any]


class ResponseDict(TypedDict, total=False):
    ok: bool
    data: dict[str, Any]
    error: str


@dataclass(frozen=True, slots=True)
class Response:
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> ResponseDict:
        out: ResponseDict = {"ok": self.ok}
        if self.ok:
            out["data"] = self.data or {}
        else:
            out["error"] = self.error or "unknown error"
        return out
