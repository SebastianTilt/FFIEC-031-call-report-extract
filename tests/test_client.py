import base64
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from callreport.ffiec import (
    AuthError,
    Credentials,
    FFIECClient,
    NoFilingError,
    ParseError,
    QuotaExceededError,
    RateLimiter,
    RequestError,
)
from callreport.ffiec.auth import token_expiry
from tests.conftest import make_token


def make_client(handler, token, sleeps=None, max_retries=3):
    return FFIECClient(
        Credentials("user1", token),
        rate_limiter=RateLimiter(3_600_000),
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        max_retries=max_retries,
    )


def encoded(data: bytes) -> httpx.Response:
    return httpx.Response(200, json=base64.b64encode(data).decode())


def test_facsimile_sends_spec_headers_and_decodes(valid_token, filing_031):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        seen["path"] = request.url.path
        return encoded(b"\xef\xbb\xbf" + filing_031)

    with make_client(handler, valid_token) as client:
        data = client.facsimile_xbrl(852218, date(2026, 3, 31))

    assert data == filing_031  # BOM stripped
    assert seen["path"] == "/public/RetrieveFacsimile"
    assert seen["userid"] == "user1"
    assert seen["authentication"] == f"Bearer {valid_token}"
    assert seen["dataseries"] == "Call"
    assert seen["reportingperiodenddate"] == "03/31/2026"
    assert (seen["fiidtype"], seen["fiid"], seen["facsimileformat"]) == ("ID_RSSD", "852218", "XBRL")


def test_reporting_periods_parses_unpadded_dates(valid_token):
    def handler(request):
        return httpx.Response(200, json=["6/30/2026", "12/31/2025"])

    with make_client(handler, valid_token) as client:
        assert client.reporting_periods() == [date(2026, 6, 30), date(2025, 12, 31)]


@pytest.mark.parametrize(
    "response",
    [
        # Observed live: a missing filing comes back as HTTP 500.
        httpx.Response(500, json="Error Code 204: Facsimile not found or unavailable"),
        httpx.Response(204),
        httpx.Response(
            400, json={"StatusCode": 400, "Message": "The requested facsimile not found or unavailable."}
        ),
    ],
)
def test_missing_filing_is_not_retried(valid_token, response):
    calls = []

    def handler(request):
        calls.append(1)
        return response

    with make_client(handler, valid_token) as client, pytest.raises(NoFilingError):
        client.facsimile_xbrl(1, date(2026, 3, 31))
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401, json={"StatusCode": 401, "Message": "Invalid JWT token"}), AuthError),
        (httpx.Response(400, json={"StatusCode": 400, "Message": "Security Token is expired."}), AuthError),
        (
            httpx.Response(
                403, json={"Message": "Out of call volume quota. Quota will be replenished in 42 min."}
            ),
            QuotaExceededError,
        ),
        (
            httpx.Response(500, json={"StatusCode": 500, "Message": "Error Code 5001: Missing Header"}),
            AuthError,
        ),
        (httpx.Response(400, json={"StatusCode": 400, "Message": "Invalid FI ID Type"}), RequestError),
    ],
)
def test_permanent_errors_fail_fast(valid_token, response, error):
    calls = []

    def handler(request):
        calls.append(1)
        return response

    with make_client(handler, valid_token) as client, pytest.raises(error):
        client.facsimile_xbrl(852218, date(2026, 3, 31))
    assert len(calls) == 1


def test_transient_errors_are_retried_with_backoff(valid_token, filing_031):
    responses = iter(
        [
            httpx.Response(500, json={"StatusCode": 500, "Message": "Error Code 5000: Database Error"}),
            httpx.Response(429, headers={"Retry-After": "7"}),
            encoded(filing_031),
        ]
    )
    sleeps = []
    with make_client(lambda r: next(responses), valid_token, sleeps) as client:
        assert client.facsimile_xbrl(852218, date(2026, 3, 31)) == filing_031
    assert 2 <= sleeps[0] <= 3  # exponential backoff with jitter
    assert sleeps[1] == 7  # honors Retry-After


def test_network_errors_give_up_after_max_retries(valid_token):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("boom")

    with (
        make_client(handler, valid_token, max_retries=2) as client,
        pytest.raises(RequestError, match="3 attempts"),
    ):
        client.facsimile_xbrl(852218, date(2026, 3, 31))
    assert len(calls) == 3


def test_rejects_bad_payload(valid_token):
    client = make_client(lambda r: httpx.Response(200, json={"not": "a string"}), valid_token)
    with client, pytest.raises(ParseError, match="base64 string"):
        client.facsimile_xbrl(852218, date(2026, 3, 31))


def test_expired_token_is_refused_before_any_request():
    expired = make_token(datetime.now(UTC) - timedelta(minutes=1))
    with pytest.raises(AuthError, match="expired"):
        make_client(lambda r: pytest.fail("no request expected"), expired)


def test_token_expiry_and_masking(valid_token):
    creds = Credentials("user1", valid_token)
    assert creds.expires_at is not None and creds.expires_at > datetime.now(UTC)
    assert valid_token not in repr(creds)
    assert token_expiry("not-a-jwt") is None


def test_credentials_from_env(monkeypatch, valid_token):
    monkeypatch.delenv("FFIEC_USERNAME", raising=False)
    monkeypatch.delenv("FFIEC_BEARER_TOKEN", raising=False)
    with pytest.raises(AuthError, match="FFIEC_USERNAME"):
        Credentials.from_env()
    monkeypatch.setenv("FFIEC_USERNAME", "user1")
    monkeypatch.setenv("FFIEC_BEARER_TOKEN", valid_token)
    assert Credentials.from_env().username == "user1"


def test_rate_limiter_bursts_then_waits_for_the_window():
    clock = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)

    limiter = RateLimiter(3, clock=lambda: clock[0], sleep=sleep)
    for _ in range(3):
        limiter.acquire()
    assert sleeps == []  # under the hourly cap: no waiting at all

    clock[0] = 100.0
    limiter.acquire()
    limiter.acquire()
    assert sleeps == [3500.0, 3500.0]  # cap reached: wait until the oldest starts are an hour old

    clock[0] = 3601.0
    limiter.acquire()
    assert sleeps == [3500.0, 3500.0]  # third original start has left the window


def test_error_body_formats(valid_token):
    # The API returns errors as a bare JSON string or an object with Message.
    body = json.dumps("Error Code 204: Facsimile not found or unavailable")
    client = make_client(lambda r: httpx.Response(500, text=body), valid_token)
    with client, pytest.raises(NoFilingError):
        client.facsimile_xbrl(1, date(2026, 3, 31))
