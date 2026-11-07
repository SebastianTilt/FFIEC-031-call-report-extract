# callreport-extract

Download FFIEC Call Report data for the banks, line items and quarters you choose, and get one
analysis-ready table with MDRM-coded columns, explicit units, a data dictionary, and a documented
reason for every missing value.

It includes a small client for the FFIEC Central Data Repository (CDR) REST API, written from the
FFIEC specification and checked against live responses, with connection reuse, a shared rate
limiter, targeted retries and safe XML parsing.

```text
$ callreport run --config config/large_banks.toml
18:05:53 INFO    7 banks x 59 quarters (2011-12-31 to 2026-06-30) = 413 filings, 50 fields, 4 workers
...
18:16:12 INFO    Filings: {'filed': 413}
18:16:12 INFO    Wrote output/call_report_large_banks.xlsx
```

## Quick start

Requires Python 3.11+.

```bash
git clone <this repo> && cd callreport-extract
python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env         # then fill in your credentials (see below)
set -a; source .env; set +a  # load them into the environment

callreport run --config config/large_banks.toml
```

Other commands and options:

```bash
callreport periods                                   # list published quarters
callreport run --config my.toml --start 2020Q1 --end 2024Q4 --format csv --out-dir data/
callreport --help
```

A full run of the example config (about 420 filings) takes a minute or two. Requests run at full
speed and only pause if a run would exceed FFIEC's hourly limit.

## Credentials

The CDR API needs a free account and a bearer token:

1. Create a Public Web Service account at the
   [CDR Public Data Distribution site](https://cdr.ffiec.gov/public/PWS/PWSPage.aspx).
2. Sign in and generate a security token. Tokens are valid for **90 days**.
3. Put your username and token in `.env` (git-ignored) or export them:

   ```bash
   export FFIEC_USERNAME=your-cdr-username
   export FFIEC_BEARER_TOKEN=eyJ...
   ```

The tool refuses to start with an expired token and warns when one expires within 7 days. It never
logs the token.

## Configuration

A run is described by a TOML file. [`config/large_banks.toml`](config/large_banks.toml) is a
complete example: 7 large banks and 50 Treasury, funding and capital items since 2011 Q4.

```toml
[run]
start = "2011Q4"          # YYYYQn or a quarter-end date
end = "latest"            # newest published quarter
workers = 12              # concurrent requests (1-16)
requests_per_hour = 2400  # shared across workers; FFIEC allows about 2,500

[output]
dir = "output"
name = "call_report_large_banks"
format = "xlsx"           # or "csv"

[[banks]]
name = "JPMorgan Chase Bank, N.A."
rssd_id = 852218          # find IDs in the FFIEC National Information Center
fed_district = "New York" # optional

[[fields]]
mdrm = "RCFD0211"
label = "U.S. Treasury securities, held-to-maturity, amortized cost (RC-B 1)"
notes = "optional; copied into the data dictionary"
```

Command-line options override the file. Look up RSSD IDs in the
[FFIEC National Information Center](https://www.ffiec.gov/npw/Institution/Index) and MDRM codes in the
[Federal Reserve MDRM dictionary](https://www.federalreserve.gov/apps/mdrm/) or the
[FFIEC 031 forms and instructions](https://www.ffiec.gov/resources/reporting-forms/ffiec031).

## Output

An `.xlsx` workbook has four sheets. With `--format csv` you get one file per sheet.

| Sheet | Contents |
|---|---|
| `data` | One row per bank and quarter: `bank_name`, `rssd_id`, `fed_district`, `report_date` (ISO), `form_type` (031/041/051), `filing_status`, then one column per MDRM code |
| `dictionary` | For each MDRM code: label, unit, how many rows have a value, missing counts by reason, and notes |
| `missing` | One row per blank cell: bank, quarter, MDRM code and reason |
| `about` | Run metadata, unit conventions and reason definitions |

**Units.**

- Dollar amounts are in **USD thousands**, the Call Report convention. The XBRL carries whole
  dollars, and every value observed is an exact multiple of 1,000.
- Ratios are **decimal fractions** as filed (`0.076257` = 7.6257%).
- Units come from each filing's XBRL `unitRef`, not from the config.

**Missing values are left blank and never filled with a substitute.** In particular, a domestic
(`RCON`) value is never used in place of a consolidated (`RCFD`) one, because the two cover
different offices. Each blank gets one of these reasons:

| Reason | Meaning |
|---|---|
| `no_filing` | FFIEC has no Call Report for this bank and quarter |
| `request_failed` | The request kept failing; rerun to fill the gap |
| `not_on_form` | The bank filed the domestic-only FFIEC 041/051, which has no consolidated or foreign-office items (`RCFD`, `RCFN`, `RCFA`, `RCFW`) |
| `not_in_filing` | The filing exists but does not contain the item: not yet (or no longer) collected, applicable only to some banks, or a derived item computed outside the filing |
| `empty_in_filing` | The item is present but has no value |

## Data notes

These were verified while building the example dataset, using FFIEC filings, the FFIEC Panel of
Reporters and the Federal Reserve MDRM dictionary:

- **Form changes.** Morgan Stanley Bank, N.A. filed the domestic-only FFIEC 041 through
  2018-03-31 and the FFIEC 031 from 2018-06-30, per the Panel of Reporters `FilingType` and the
  filings' own taxonomy. Its consolidated items before then are `not_on_form`. Its first 031
  filing also has no Schedule RC-H domestic-office items.
- **RC-A consolidated balances** (`RCFD0082`, `RCFD0070`) appear in filings only from
  2018-06-30. Before that, the MDRM dictionary lists them as derived items for the FFIEC 031.
- **Revised regulatory capital** (`RCFA`/`RCFW` codes) starts 2014-03-31, total leverage exposure
  and the supplementary leverage ratio start 2016-09-30 (MDRM start dates), and `RCFW7206` is
  reported only by advanced-approaches banks that exited parallel run.
- **Scope.** Call Reports cover the bank, not its holding company (that is the FR Y-9C).
- **Line references** in labels come from the 2026 forms; line numbers have changed over time.
- **Amendments.** Values are the latest version of each filing on the day you run the tool.

## How the FFIEC client works

The client in [`src/callreport/ffiec/`](src/callreport/ffiec/) implements only the two endpoints
this tool needs (`RetrieveReportingPeriods` and `RetrieveFacsimile`). It is based on the FFIEC
specification
[SIS611 v1.10](https://cdr.ffiec.gov/public/Files/SIS611_-_Retrieve_Public_Data_via_Web_Service.pdf)
and on observed server behavior:

- Every parameter, including credentials, is sent as an HTTP header (`UserID`,
  `Authentication: Bearer ...`, `fiId`, `reportingPeriodEndDate`, ...).
- A facsimile arrives as a JSON string holding the base64-encoded XBRL document.
- **Errors are classified by message, not just by status code**, because the server's codes differ
  from what their names suggest:

  | Response | Meaning | Handling |
  |---|---|---|
  | `500 "Error Code 204: Facsimile not found..."` (observed), `204`, `400 "...not found..."` | No filing | Recorded, not retried |
  | `401`, `400 "Security Token..."`, `403`, `500 "Error Code 5001/5003"` | Credential or request problem | Run stops |
  | `403 "Out of call volume quota"` | Hourly quota used up | Run stops |
  | Other `400` | Rejected request | Recorded as `request_failed`, not retried |
  | `429`, other `5xx`, timeouts | Transient | Retried with exponential backoff, honoring `Retry-After` |

- One `httpx.Client` and one thread-safe rate limiter are shared by all worker threads. The limiter
  caps request starts per rolling hour (2,400 by default, under FFIEC's ~2,500) without spacing
  them out, so runs within the hourly budget go at full speed.
- XBRL is parsed with `defusedxml`, which blocks entity expansion and external entities. Each filing
  is checked for the expected RSSD ID and report date, and conflicting duplicate facts are rejected.

## Development

```bash
pip install -e ".[dev]"
pytest          # offline; uses trimmed real filings in tests/fixtures
ruff check . && ruff format --check .
```

`tests/test_crosscheck.py` compares the parser, fact by fact, with the independent
[`ffiec-data-connect`](https://pypi.org/project/ffiec-data-connect/) library. It runs only when
that package is installed, as in CI on Python 3.13.

Exit codes: `0` success, `1` configuration or credential error, `2` finished but some requests
failed (those rows are marked `request_failed`).

## License

MIT. See [LICENSE](LICENSE). Call Report data is public information published by the FFIEC.
This project is not affiliated with the FFIEC or any of its member agencies.
