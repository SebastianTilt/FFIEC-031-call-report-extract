from datetime import date

import pytest

from callreport.ffiec import ParseError, parse_facsimile


def test_parses_filing_metadata(filing_031):
    filing = parse_facsimile(filing_031)
    assert filing.rssd_id == 852218
    assert filing.report_date == date(2026, 3, 31)
    assert filing.form_type == "031"


def test_detects_domestic_only_form(filing_041):
    filing = parse_facsimile(filing_041)
    assert (filing.rssd_id, filing.form_type) == (1456501, "041")
    assert not any(code.startswith(("RCFD", "RCFN", "RCFA", "RCFW")) for code in filing.facts)


@pytest.mark.parametrize(
    ("mdrm", "value", "unit"),
    [
        ("RCFD2170", 4_016_571_000_000, "usd"),  # whole dollars, as in the XBRL
        ("RIAD4521", -2_000_000, "usd"),  # negative, duration (CD) context
        ("RCFA7204", 0.076257, "ratio"),  # decimal fraction, not a percent
        ("RCFDJ262", 53328, "number"),
        ("RCONPG75", True, "bool"),
        ("TE02N528", "www.jpmorgan.com", "text"),
    ],
)
def test_typed_values(filing_031, mdrm, value, unit):
    fact = parse_facsimile(filing_031).facts[mdrm]
    assert (fact.value, fact.unit) == (value, unit)


def test_empty_text_element_is_missing(filing_041):
    fact = parse_facsimile(filing_041).facts["TEXTG581"]
    assert fact.value is None


def test_bom_and_nil_are_handled(filing_031):
    data = b"\xef\xbb\xbf" + filing_031.replace(
        b'<cc:RCFD2170 contextRef="CI_852218_2026-03-31" unitRef="USD" decimals="0">4016571000000',
        b'<cc:RCFD2170 contextRef="CI_852218_2026-03-31" unitRef="USD" xsi:nil="true">',
    )
    assert parse_facsimile(data).facts["RCFD2170"].value is None


def test_rejects_entity_expansion():
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><xbrl>&a;</xbrl>'
    with pytest.raises(ParseError, match="unsafe"):
        parse_facsimile(evil)


def test_rejects_non_xbrl():
    with pytest.raises(ParseError, match="Not an XBRL"):
        parse_facsimile(b"<html></html>")


def test_rejects_conflicting_duplicate_facts(filing_031):
    duplicate = b'<cc:RCFD2170 contextRef="CI_852218_2026-03-31" unitRef="USD" decimals="0">1</cc:RCFD2170>'
    data = filing_031.replace(b"</xbrl>", duplicate + b"</xbrl>")
    with pytest.raises(ParseError, match="Conflicting"):
        parse_facsimile(data)


def test_rejects_unknown_unit(filing_031):
    data = filing_031.replace(b'unitRef="PURE"', b'unitRef="EUR"', 1)
    with pytest.raises(ParseError, match="unknown unitRef"):
        parse_facsimile(data)
