"""Exceptions raised by the FFIEC client."""


class FFIECError(Exception):
    """Base class for all FFIEC client errors."""


class AuthError(FFIECError):
    """Credentials are missing, invalid, or expired. Not retryable."""


class QuotaExceededError(FFIECError):
    """The account's hourly call quota is exhausted. Not retryable within a run."""


class NoFilingError(FFIECError):
    """No facsimile exists for the requested institution and period."""


class RequestError(FFIECError):
    """The request was rejected or kept failing after retries."""


class ParseError(FFIECError):
    """A response could not be decoded or parsed."""
