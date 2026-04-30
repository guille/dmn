# pyright: basic

import pytest

from dmn.api.protocol import Request, Response


class TestResponse:
    def test_ok_response_to_dict(self):
        r = Response(ok=True, data={"key": "value"})
        assert r.to_dict() == {"ok": True, "data": {"key": "value"}}

    def test_ok_response_no_data(self):
        r = Response(ok=True)
        assert r.to_dict() == {"ok": True, "data": {}}

    def test_error_response_to_dict(self):
        r = Response(ok=False, error="something broke")
        assert r.to_dict() == {"ok": False, "error": "something broke"}

    def test_error_response_no_message(self):
        r = Response(ok=False)
        assert r.to_dict() == {"ok": False, "error": "unknown error"}

    def test_response_is_frozen(self):
        r = Response(ok=True)
        with pytest.raises(AttributeError):
            r.ok = False  # pyright: ignore[reportAttributeAccessIssue]


class TestRequest:
    def test_request_is_frozen(self):
        req = Request(type="EXEC", payload={"command": "ls"})
        assert req.type == "EXEC"
        assert req.payload == {"command": "ls"}
        with pytest.raises(AttributeError):
            req.type = "QUERY"  # pyright: ignore[reportAttributeAccessIssue]
