import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    """Trimmed real FFIEC facsimiles (public data)."""
    return (FIXTURES / name).read_bytes()


def make_token(expires: datetime) -> str:
    """An unsigned JWT shaped like the ones FFIEC issues."""

    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'exp': int(expires.timestamp())})}."


@pytest.fixture
def valid_token() -> str:
    return make_token(datetime.now(UTC) + timedelta(days=60))


@pytest.fixture
def filing_031() -> bytes:
    return fixture_bytes("031_852218_2026-03-31.xml")


@pytest.fixture
def filing_041() -> bytes:
    return fixture_bytes("041_1456501_2015-12-31.xml")
