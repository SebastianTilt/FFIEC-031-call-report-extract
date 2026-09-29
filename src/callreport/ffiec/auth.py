"""FFIEC CDR credentials.

The CDR REST API authenticates with two headers: ``UserID`` and
``Authentication: Bearer <token>`` (FFIEC SIS611, section 2.1). Tokens are
generated in the CDR portal and are valid for 90 days.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .errors import AuthError

USERNAME_ENV = "FFIEC_USERNAME"
TOKEN_ENV = "FFIEC_BEARER_TOKEN"


def token_expiry(token: str) -> datetime | None:
    """Return the ``exp`` claim of a JWT as an aware UTC datetime, if present.

    FFIEC tokens are unsigned JWTs, so reading the claim needs only base64 and
    JSON. The token is never validated locally; the server does that.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
    except (binascii.Error, ValueError, KeyError, TypeError):
        return None


@dataclass(frozen=True)
class Credentials:
    username: str
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.username or not self.token:
            raise AuthError("FFIEC username and bearer token are both required")

    @classmethod
    def from_env(cls) -> Credentials:
        username = os.environ.get(USERNAME_ENV, "").strip()
        token = os.environ.get(TOKEN_ENV, "").strip()
        if not username or not token:
            raise AuthError(f"Set {USERNAME_ENV} and {TOKEN_ENV} (see README: 'Credentials').")
        return cls(username, token)

    @property
    def expires_at(self) -> datetime | None:
        return token_expiry(self.token)

    def is_expired(self, now: datetime | None = None) -> bool:
        expires = self.expires_at
        return expires is not None and expires <= (now or datetime.now(UTC))

    def headers(self) -> dict[str, str]:
        return {"UserID": self.username, "Authentication": f"Bearer {self.token}"}
