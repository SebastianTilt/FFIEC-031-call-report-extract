from datetime import date

import pytest

from callreport.periods import parse_quarter, select_periods

AVAILABLE = [date(2026, 6, 30), date(2026, 3, 31), date(2025, 12, 31), date(2025, 9, 30)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2012Q1", date(2012, 3, 31)), ("2011q4", date(2011, 12, 31)), ("2020-06-30", date(2020, 6, 30))],
)
def test_parse_quarter(text, expected):
    assert parse_quarter(text) == expected


@pytest.mark.parametrize("text", ["2012Q5", "2012-02-28", "Q1 2012", ""])
def test_parse_quarter_rejects(text):
    with pytest.raises(ValueError):
        parse_quarter(text)


def test_select_latest_uses_published_periods():
    assert select_periods(AVAILABLE, "2025Q4", "latest") == [
        date(2026, 6, 30),
        date(2026, 3, 31),
        date(2025, 12, 31),
    ]


def test_select_skips_unpublished_quarters():
    # 2026Q3 has not been published, so it is not requested.
    assert select_periods(AVAILABLE, "2026Q2", "2026Q3") == [date(2026, 6, 30)]


def test_select_rejects_reversed_or_empty_ranges():
    with pytest.raises(ValueError, match="after"):
        select_periods(AVAILABLE, "2026Q2", "2025Q4")
    with pytest.raises(ValueError, match="No published"):
        select_periods(AVAILABLE, "2010Q1", "2010Q4")
