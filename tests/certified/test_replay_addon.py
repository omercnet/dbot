"""The replay matcher: form, query and header constraints must hold exactly, or the request goes unmatched."""

from typing import Any

import pytest
from mitmproxy.test import tflow

from tests.certified.replay_addon import UNMATCHED_STATUS, Replay, match

HASH = "094fd325049b8a9cf6d3e5ef2a6d4cc6a567d7d49c35f8bb8dd9e3c6acf3d78d"
COMMENT_FORM = {"query": "add_comment", "sha256_hash": HASH, "comment": "test comment"}
ROUTES: list[dict[str, Any]] = [
    {"method": "POST", "host": "mb-api.abuse.ch", "path": "/api/v1/", "form": COMMENT_FORM},
    {"method": "GET", "host": "api.example", "path": "/check", "query": {"ip": "1.1.1.1"}, "headers": {"Key": "k"}},
]


def post(form: dict[str, str]) -> dict[str, Any] | None:
    return match(ROUTES, "POST", "mb-api.abuse.ch", "/api/v1/", {}, {}, "", form)


def test_exact_form_matches_and_extra_fields_are_allowed() -> None:
    assert post(COMMENT_FORM) is ROUTES[0]
    assert post(COMMENT_FORM | {"extra": "x"}) is ROUTES[0]


@pytest.mark.parametrize("field", ["query", "sha256_hash", "comment"])
def test_changed_form_field_is_unmatched(field: str) -> None:
    assert post(COMMENT_FORM | {field: COMMENT_FORM[field] + "x"}) is None


@pytest.mark.parametrize("field", ["query", "sha256_hash", "comment"])
def test_missing_form_field_is_unmatched(field: str) -> None:
    assert post({k: v for k, v in COMMENT_FORM.items() if k != field}) is None


def test_substring_of_a_field_value_is_unmatched() -> None:
    assert post(COMMENT_FORM | {"comment": "not a test comment"}) is None


def test_query_and_headers_must_match() -> None:
    def get(query: dict[str, str], headers: dict[str, str]) -> dict[str, Any] | None:
        return match(ROUTES, "GET", "api.example", "/check", query, headers, "")

    assert get({"ip": "1.1.1.1"}, {"key": "k"}) is ROUTES[1]  # header names are case-insensitive
    assert get({"ip": "1.1.1.2"}, {"Key": "k"}) is None
    assert get({"ip": "1.1.1.1"}, {"Key": "other"}) is None


def multipart(fields: dict[str, str]) -> tuple[str, bytes]:
    parts = b"".join(
        b'--b\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (k.encode(), v.encode())
        for k, v in fields.items()
    )
    return "multipart/form-data; boundary=b", parts + b"--b--\r\n"


def urlencoded(fields: dict[str, str]) -> tuple[str, bytes]:
    return "application/x-www-form-urlencoded", "&".join(
        f"{k}={v.replace(' ', '+')}" for k, v in fields.items()
    ).encode()


@pytest.mark.parametrize("encode", [multipart, urlencoded])
def test_request_parses_form_bodies(encode: Any) -> None:
    """Replay.request feeds the parsed multipart/urlencoded body to the form constraint, not just raw text."""
    replay = Replay()
    replay.routes = [ROUTES[0] | {"response": {"status": 200, "body": {"query_status": "success"}}}]

    def send(fields: dict[str, str]) -> int:
        flow = tflow.tflow()
        flow.request.method, flow.request.host, flow.request.path = "POST", "mb-api.abuse.ch", "/api/v1/"
        flow.request.headers["content-type"], flow.request.content = encode(fields)
        replay.request(flow)
        assert flow.response is not None
        return int(flow.response.status_code)

    assert send(COMMENT_FORM) == 200
    assert send(COMMENT_FORM | {"sha256_hash": "0" * 64}) == UNMATCHED_STATUS
