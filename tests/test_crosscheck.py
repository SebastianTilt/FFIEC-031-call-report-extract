"""Compare the parser with the independent ffiec-data-connect implementation.

Optional: runs only when ffiec-data-connect is installed
(``pip install ffiec-data-connect``). It uses that library's internal XBRL
function, so it may need updating if the library changes.
"""

import math

import pytest

from callreport.ffiec import parse_facsimile

xbrl_processor = pytest.importorskip("ffiec_data_connect.xbrl_processor")


def library_value(record):
    value = record[f"{record['data_type']}_data"]
    if value is None or (not isinstance(value, str) and value != value):  # NaN / pd.NA
        return None
    try:
        return None if math.isnan(value) else value
    except TypeError:
        return value


@pytest.mark.parametrize("name", ["031_852218_2026-03-31.xml", "041_1456501_2015-12-31.xml"])
def test_matches_ffiec_data_connect(name):
    from tests.conftest import fixture_bytes

    data = fixture_bytes(name)
    mine = parse_facsimile(data).facts
    theirs = {r["mdrm"]: r for r in xbrl_processor._process_xml(data, "string_original", False)}

    assert set(mine) == set(theirs)
    for mdrm, fact in mine.items():
        expected = library_value(theirs[mdrm])
        if fact.unit == "usd":
            # The library converts dollars to thousands.
            assert fact.value // 1000 == expected, mdrm
        elif fact.unit == "text" and fact.value is None:
            # The library turns an empty element into the string "None".
            assert expected in (None, "None"), mdrm
        else:
            assert fact.value == pytest.approx(expected), mdrm
