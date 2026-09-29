"""Minimal client for the FFIEC CDR Public Data Distribution REST API.

Written from the FFIEC specification "CDR-PDD-SIS-611 v1.10: Retrieve Public
Data via Web Service" and from observed server behavior. Only the two endpoints
this tool needs are implemented.

Notable server behavior (verified against the live API, September 2026):

* Every parameter is sent as an HTTP header, not a query parameter.
* ``RetrieveFacsimile`` returns a JSON string holding the base64-encoded file.
* A missing filing is reported as HTTP 500 with the body
  ``"Error Code 204: Facsimile not found or unavailable"``; the spec also lists
  HTTP 204 and a 400 variant, so all three are treated as "no filing".
* Quota exhaustion is HTTP 403 ("Out of call volume quota"), not 429.
"""

from __future__ import annotations

import base64
import binascii
import logging
import random
import time
from collections.abc import Callable
from datetime import date, datetime

import httpx

from .auth import Credentials
from .errors import AuthError, FFIECError, NoFilingError, ParseError, QuotaExceededError, RequestError
from .ratelimit import RateLimiter

log = logging.getLogger(__name__)

BASE_URL = "https://ffieccdr.azure-api.us/public/"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def format_period(period: date) -> str:
    """Format a report date the way the API expects it (MM/DD/YYYY)."""
    return period.strftime("%m/%d/%Y")


def _message(response: httpx.Response) -> str:
    """Extract the error message from the API's JSON string or object body."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()
    if isinstance(body, dict):
        return str(body.get("Message") or body.get("message") or body)
    return str(body)


def classify_error(response: httpx.Response) -> FFIECError:
    """Map a non-200 response to an exception. ``RequestError`` may be retried."""
    status = response.status_code
    message = _message(response)
    lowered = message.lower()

    if status == 204 or "not found or unavailable" in lowered or "error code 204" in lowered:
        return NoFilingError(message or "Facsimile not found or unavailable")
    if status == 401 or "security token" in lowered:
        return AuthError(f"HTTP {status}: {message}")
    if status == 403:
        if "quota" in lowered:
            return QuotaExceededError(f"HTTP 403: {message}")
        return AuthError(f"HTTP 403: {message}")
    if "error code 5001" in lowered or "error code 5003" in lowered:
        # Missing/invalid header or access denied: retrying cannot help.
        return AuthError(f"HTTP {status}: {message}")
    return RequestError(f"HTTP {status}: {message}")


class FFIECClient:
    """Reuses one HTTP connection pool and one rate limiter for all requests.

    Safe to share across threads. Use as a context manager, or call ``close``.
    """

    def __init__(
        self,
        credentials: Credentials,
        *,
        rate_limiter: RateLimiter | None = None,
        max_retries: int = 3,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if credentials.is_expired():
            raise AuthError(
                f"FFIEC bearer token expired at {credentials.expires_at:%Y-%m-%d %H:%M} UTC; "
                "generate a new one in the CDR portal."
            )
        self._credentials = credentials
        self._rate_limiter = rate_limiter or RateLimiter()
        self._max_retries = max_retries
        self._sleep = sleep
        self._http = httpx.Client(base_url=BASE_URL, timeout=timeout, transport=transport)

    def __enter__(self) -> FFIECClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def reporting_periods(self) -> list[date]:
        """All Call Report period-end dates available, newest first."""
        response = self._get("RetrieveReportingPeriods", {"dataSeries": "Call"})
        try:
            values = response.json()
            return [datetime.strptime(v, "%m/%d/%Y").date() for v in values]
        except (ValueError, TypeError) as exc:
            raise ParseError(f"Unexpected RetrieveReportingPeriods response: {exc}") from exc

    def facsimile_xbrl(self, rssd_id: int, period: date) -> bytes:
        """Raw XBRL instance document for one institution and period."""
        response = self._get(
            "RetrieveFacsimile",
            {
                "dataSeries": "Call",
                "reportingPeriodEndDate": format_period(period),
                "fiIdType": "ID_RSSD",
                "fiId": str(rssd_id),
                "facsimileFormat": "XBRL",
            },
        )
        try:
            encoded = response.json()
            if not isinstance(encoded, str):
                raise TypeError(f"expected a base64 string, got {type(encoded).__name__}")
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ParseError(f"Unexpected RetrieveFacsimile response: {exc}") from exc
        return data.removeprefix(b"\xef\xbb\xbf")

    def _get(self, endpoint: str, params: dict[str, str]) -> httpx.Response:
        headers = {**self._credentials.headers(), **params}
        for attempt in range(self._max_retries + 1):
            self._rate_limiter.acquire()
            try:
                response = self._http.get(endpoint, headers=headers)
            except httpx.TransportError as exc:
                error: FFIECError = RequestError(f"{type(exc).__name__}: {exc}")
                retry_after = None
            else:
                if response.status_code == 200:
                    return response
                error = classify_error(response)
                if not isinstance(error, RequestError) or response.status_code not in RETRYABLE_STATUS:
                    raise error
                retry_after = response.headers.get("Retry-After")

            if attempt == self._max_retries:
                raise RequestError(f"{endpoint} failed after {attempt + 1} attempts: {error}")
            delay = _backoff(attempt, retry_after)
            log.warning("%s: %s; retrying in %.1fs", endpoint, error, delay)
            self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after and retry_after.isdigit():
        return float(retry_after)
    return min(60.0, 2.0 * 2**attempt) + random.uniform(0, 1)
