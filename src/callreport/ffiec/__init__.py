"""Thin client for the FFIEC CDR Public Data Distribution REST API."""

from .auth import Credentials
from .client import FFIECClient
from .errors import AuthError, FFIECError, NoFilingError, ParseError, QuotaExceededError, RequestError
from .ratelimit import RateLimiter
from .xbrl import Fact, Filing, parse_facsimile

__all__ = [
    "AuthError",
    "Credentials",
    "FFIECClient",
    "FFIECError",
    "Fact",
    "Filing",
    "NoFilingError",
    "ParseError",
    "QuotaExceededError",
    "RateLimiter",
    "RequestError",
    "parse_facsimile",
]
