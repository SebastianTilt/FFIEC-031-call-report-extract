"""Fetch filings for every bank and period and shape them into rows."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Protocol

from .config import Bank, Field
from .ffiec import AuthError, FFIECError, Filing, NoFilingError, QuotaExceededError, parse_facsimile
from .ffiec.xbrl import Unit, Value

log = logging.getLogger(__name__)

# Consolidated (RCFD), foreign-office (RCFN) and consolidated capital (RCFA,
# RCFW) items exist only on the FFIEC 031. Banks filing the domestic-only
# FFIEC 041 or 051 report RCON/RCOA/RCOW items instead. Verified against a
# 2015 FFIEC 041 facsimile, which contains no RCFD/RCFN/RCFA/RCFW elements.
CONSOLIDATED_PREFIXES = ("RCFD", "RCFN", "RCFA", "RCFW")
DOMESTIC_ONLY_FORMS = ("041", "051")


class Status(StrEnum):
    FILED = "filed"
    NO_FILING = "no_filing"
    REQUEST_FAILED = "request_failed"


class Reason(StrEnum):
    NO_FILING = "no_filing"
    REQUEST_FAILED = "request_failed"
    NOT_ON_FORM = "not_on_form"
    NOT_IN_FILING = "not_in_filing"
    EMPTY_IN_FILING = "empty_in_filing"


REASON_DESCRIPTIONS = {
    Reason.NO_FILING: "FFIEC has no Call Report for this bank and period.",
    Reason.REQUEST_FAILED: "The request kept failing; rerun to fill the gap (see the log).",
    Reason.NOT_ON_FORM: (
        "The bank filed the domestic-only FFIEC 041/051, which does not collect "
        "consolidated or foreign-office items (RCFD/RCFN/RCFA/RCFW). Domestic "
        "(RCON) values are not substituted because their coverage differs."
    ),
    Reason.NOT_IN_FILING: (
        "The filing does not contain this item: it was not yet (or no longer) "
        "collected in that period, applies only to some banks, or is a derived "
        "item the agencies compute outside the filing."
    ),
    Reason.EMPTY_IN_FILING: "The item is present in the filing but has no value.",
}


class FacsimileSource(Protocol):
    def facsimile_xbrl(self, rssd_id: int, period: date) -> bytes: ...


@dataclass
class Row:
    bank: Bank
    report_date: date
    status: Status
    form_type: str | None = None
    values: dict[str, Value] = field(default_factory=dict)
    units: dict[str, Unit] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class MissingValue:
    bank: Bank
    report_date: date
    mdrm: str
    reason: Reason


@dataclass
class Result:
    rows: list[Row]
    missing: list[MissingValue]

    @property
    def failed(self) -> list[Row]:
        return [r for r in self.rows if r.status is Status.REQUEST_FAILED]


def extract(
    source: FacsimileSource,
    banks: Sequence[Bank],
    fields: Sequence[Field],
    periods: Sequence[date],
    *,
    workers: int = 4,
    on_progress: Callable[[int, int], None] | None = None,
) -> Result:
    """Collect one row per (bank, period).

    Credential and quota errors abort the run, since every later request would
    fail too. Any other per-request failure is recorded on its row.
    """
    tasks = [(bank, period) for period in periods for bank in banks]
    rows: list[Row] = []
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {executor.submit(_fetch, source, bank, period): (bank, period) for bank, period in tasks}
        for done, future in enumerate(as_completed(futures), start=1):
            bank, period = futures[future]
            try:
                filing = future.result()
            except (AuthError, QuotaExceededError):
                raise
            except NoFilingError:
                rows.append(Row(bank, period, Status.NO_FILING))
            except FFIECError as exc:
                log.error("%s (%s) %s: %s", bank.name, bank.rssd_id, period, exc)
                rows.append(Row(bank, period, Status.REQUEST_FAILED, error=str(exc)))
            else:
                rows.append(_filed_row(bank, period, filing, fields))
            if on_progress:
                on_progress(done, len(tasks))
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    rows.sort(key=lambda r: (r.bank.name, -r.report_date.toordinal()))
    missing = [m for row in rows for m in _missing(row, fields)]
    return Result(rows, missing)


def _fetch(source: FacsimileSource, bank: Bank, period: date) -> Filing:
    filing = parse_facsimile(source.facsimile_xbrl(bank.rssd_id, period))
    if filing.rssd_id != bank.rssd_id or filing.report_date != period:
        raise FFIECError(
            f"Filing identifies RSSD {filing.rssd_id} on {filing.report_date}, "
            f"expected {bank.rssd_id} on {period}"
        )
    return filing


def _filed_row(bank: Bank, period: date, filing: Filing, fields: Sequence[Field]) -> Row:
    row = Row(bank, period, Status.FILED, form_type=filing.form_type)
    for f in fields:
        fact = filing.facts.get(f.mdrm)
        if fact is not None:
            row.values[f.mdrm] = fact.value
            row.units[f.mdrm] = fact.unit
    return row


def _missing(row: Row, fields: Sequence[Field]) -> list[MissingValue]:
    out = []
    for f in fields:
        if row.values.get(f.mdrm) is not None:
            continue
        if row.status is Status.NO_FILING:
            reason = Reason.NO_FILING
        elif row.status is Status.REQUEST_FAILED:
            reason = Reason.REQUEST_FAILED
        elif f.mdrm in row.values:
            reason = Reason.EMPTY_IN_FILING
        elif row.form_type in DOMESTIC_ONLY_FORMS and f.mdrm.startswith(CONSOLIDATED_PREFIXES):
            reason = Reason.NOT_ON_FORM
        else:
            reason = Reason.NOT_IN_FILING
        out.append(MissingValue(row.bank, row.report_date, f.mdrm, reason))
    return out
