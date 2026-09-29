import csv
from datetime import date

import pytest
from openpyxl import load_workbook

from callreport.config import Bank, Field
from callreport.export import data_table, export
from callreport.ffiec import AuthError, NoFilingError, RequestError
from callreport.pipeline import Reason, Status, extract

JPM = Bank("JPMorgan Chase Bank, N.A.", 852218, "New York")
MS = Bank("Morgan Stanley Bank, N.A.", 1456501, "New York")
FIELDS = [
    Field("RCFD0211", "Treasury HTM amortized cost"),
    Field("RCON0211", "Treasury HTM amortized cost, domestic"),
    Field("RCFA7204", "Tier 1 leverage ratio"),
    Field("RCFDA549", "Maturity <= 3 months"),
    Field("RCON9999", "Not a real item"),
]


class FakeSource:
    """Serves the trimmed real filings; everything else has no filing."""

    def __init__(self, filings, errors=None):
        self.filings = filings
        self.errors = errors or {}

    def facsimile_xbrl(self, rssd_id, period):
        key = (rssd_id, period)
        if key in self.errors:
            raise self.errors[key]
        if key not in self.filings:
            raise NoFilingError("Facsimile not found or unavailable")
        return self.filings[key]


@pytest.fixture
def source(filing_031, filing_041):
    return FakeSource(
        {
            (852218, date(2026, 3, 31)): filing_031,
            (1456501, date(2015, 12, 31)): filing_041,
        }
    )


def reasons(result, bank, period):
    return {m.mdrm: m.reason for m in result.missing if m.bank == bank and m.report_date == period}


def test_031_filing_values_and_reasons(source):
    result = extract(source, [JPM], FIELDS, [date(2026, 3, 31)], workers=1)
    row = result.rows[0]
    assert (row.status, row.form_type) == (Status.FILED, "031")
    assert row.values["RCFD0211"] == 141_204_000_000
    assert row.values["RCFA7204"] == 0.076257
    assert reasons(result, JPM, date(2026, 3, 31)) == {"RCON9999": Reason.NOT_IN_FILING}


def test_041_filing_flags_consolidated_items_without_substituting(source):
    period = date(2015, 12, 31)
    result = extract(source, [MS], FIELDS, [period], workers=1)
    row = result.rows[0]
    assert row.form_type == "041"
    assert row.values["RCON0211"] == 1_001_000_000
    assert "RCFD0211" not in row.values  # no RCON-for-RCFD substitution
    assert reasons(result, MS, period) == {
        "RCFD0211": Reason.NOT_ON_FORM,
        "RCFA7204": Reason.NOT_ON_FORM,
        "RCFDA549": Reason.NOT_ON_FORM,
        "RCON9999": Reason.NOT_IN_FILING,
    }


def test_no_filing_and_failed_requests_are_recorded(source):
    period = date(2026, 3, 31)
    source.errors[(1456501, period)] = RequestError("HTTP 500: Error Code 5000")
    other = Bank("Nobody", 1, None)
    result = extract(source, [JPM, MS, other], FIELDS, [period], workers=3)
    statuses = {r.bank.rssd_id: r.status for r in result.rows}
    assert statuses == {852218: Status.FILED, 1456501: Status.REQUEST_FAILED, 1: Status.NO_FILING}
    assert set(reasons(result, MS, period).values()) == {Reason.REQUEST_FAILED}
    assert set(reasons(result, other, period).values()) == {Reason.NO_FILING}
    assert len(result.failed) == 1


def test_filing_for_wrong_bank_is_rejected(filing_031):
    wrong = FakeSource({(1456501, date(2026, 3, 31)): filing_031})
    result = extract(wrong, [MS], FIELDS, [date(2026, 3, 31)], workers=1)
    assert result.rows[0].status is Status.REQUEST_FAILED
    assert "expected 1456501" in result.rows[0].error


def test_auth_errors_abort_the_run(source):
    source.errors[(852218, date(2026, 3, 31))] = AuthError("HTTP 401")
    with pytest.raises(AuthError):
        extract(source, [JPM], FIELDS, [date(2026, 3, 31)], workers=1)


def test_data_table_uses_codes_and_usd_thousands(source):
    result = extract(source, [JPM], FIELDS, [date(2026, 3, 31)], workers=1)
    header, row = data_table(result.rows, FIELDS)
    assert header[:6] == ["bank_name", "rssd_id", "fed_district", "report_date", "form_type", "filing_status"]
    assert header[6:] == [f.mdrm for f in FIELDS]
    values = dict(zip(header, row, strict=True))
    assert values["RCFD0211"] == 141_204_000  # USD thousands
    assert values["RCFA7204"] == 0.076257
    assert values["RCON9999"] is None
    assert values["report_date"] == "2026-03-31"


def test_export_xlsx_and_csv(tmp_path, source):
    periods = [date(2026, 3, 31), date(2015, 12, 31)]
    result = extract(source, [JPM, MS], FIELDS, periods, workers=2)
    about = {"generated_at": "test"}

    (xlsx,) = export(result, FIELDS, tmp_path, "out", "xlsx", about)
    workbook = load_workbook(xlsx)
    assert workbook.sheetnames == ["data", "dictionary", "missing", "about"]
    dictionary = {r[0]: r for r in workbook["dictionary"].iter_rows(min_row=2, values_only=True)}
    assert dictionary["RCFD0211"][2] == "USD thousands"
    assert dictionary["RCFA7204"][2].startswith("ratio")
    assert dictionary["RCON9999"][2] == "(never reported)"

    paths = export(result, FIELDS, tmp_path, "out", "csv", about)
    assert [p.name for p in paths] == [
        "out_data.csv",
        "out_dictionary.csv",
        "out_missing.csv",
        "out_about.csv",
    ]
    with paths[2].open() as fh:
        missing = list(csv.DictReader(fh))
    assert {"bank_name", "rssd_id", "report_date", "mdrm", "reason"} == set(missing[0])
    assert len(missing) == len(result.missing)


def test_formula_like_text_is_written_as_text(tmp_path, source):
    result = extract(source, [JPM], FIELDS, [date(2026, 3, 31)], workers=1)
    result.rows[0].values["RCON9999"] = '=HYPERLINK("http://x")'
    result.rows[0].units["RCON9999"] = "text"
    (xlsx,) = export(result, FIELDS, tmp_path, "out", "xlsx", {})
    cell = load_workbook(xlsx)["data"].cell(row=2, column=6 + len(FIELDS))
    assert cell.data_type == "s"
