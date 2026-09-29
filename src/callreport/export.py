"""Write results as an Excel workbook or a set of CSV files."""

from __future__ import annotations

import csv
import logging
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .config import Field
from .ffiec.xbrl import Unit, Value
from .pipeline import REASON_DESCRIPTIONS, Result, Row

log = logging.getLogger(__name__)

UNIT_LABELS: dict[Unit, str] = {
    "usd": "USD thousands",
    "ratio": "ratio, decimal fraction (0.0763 = 7.63%)",
    "number": "count",
    "bool": "true/false",
    "text": "text",
}
ROW_COLUMNS = ["bank_name", "rssd_id", "fed_district", "report_date", "form_type", "filing_status"]

Table = list[list[object]]


def export(
    result: Result, fields: Sequence[Field], out_dir: Path, name: str, fmt: str, about: dict[str, str]
) -> list[Path]:
    tables = {
        "data": data_table(result.rows, fields),
        "dictionary": dictionary_table(result, fields),
        "missing": missing_table(result),
        "about": [["key", "value"], *about.items(), *_reason_rows()],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    if fmt == "xlsx":
        return [_write_xlsx(out_dir / f"{name}.xlsx", tables)]
    return [_write_csv(out_dir / f"{name}_{sheet}.csv", rows) for sheet, rows in tables.items()]


def data_table(rows: Sequence[Row], fields: Sequence[Field]) -> Table:
    table: Table = [ROW_COLUMNS + [f.mdrm for f in fields]]
    for row in rows:
        values = [_display(row.values.get(f.mdrm), row.units.get(f.mdrm)) for f in fields]
        table.append(
            [
                row.bank.name,
                row.bank.rssd_id,
                row.bank.fed_district,
                row.report_date.isoformat(),
                row.form_type,
                row.status.value,
                *values,
            ]
        )
    return table


def dictionary_table(result: Result, fields: Sequence[Field]) -> Table:
    units: dict[str, set[Unit]] = defaultdict(set)
    for row in result.rows:
        for mdrm, unit in row.units.items():
            units[mdrm].add(unit)
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    for m in result.missing:
        reasons[m.mdrm][m.reason.value] += 1

    table: Table = [
        ["mdrm", "label", "unit", "rows_with_value", "rows_missing", "missing_by_reason", "notes"]
    ]
    for f in fields:
        observed = sorted(units[f.mdrm])
        if len(observed) > 1:
            log.warning("%s was reported with more than one unit: %s", f.mdrm, observed)
        missing = sum(reasons[f.mdrm].values())
        table.append(
            [
                f.mdrm,
                f.label,
                " | ".join(UNIT_LABELS[u] for u in observed) or "(never reported)",
                len(result.rows) - missing,
                missing,
                ", ".join(f"{r}: {n}" for r, n in sorted(reasons[f.mdrm].items())),
                f.notes,
            ]
        )
    return table


def missing_table(result: Result) -> Table:
    table: Table = [["bank_name", "rssd_id", "report_date", "mdrm", "reason"]]
    for m in result.missing:
        table.append([m.bank.name, m.bank.rssd_id, m.report_date.isoformat(), m.mdrm, m.reason.value])
    return table


def run_metadata(periods: Sequence, config_path: Path) -> dict[str, str]:
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "FFIEC Central Data Repository, RetrieveFacsimile (XBRL), Call Report series",
        "config": config_path.name,
        "periods": f"{min(periods).isoformat()} to {max(periods).isoformat()} ({len(periods)} quarters)",
        "usd_values": "Reported in USD thousands, the Call Report convention (XBRL carries whole dollars).",
        "ratios": "Decimal fractions as filed, e.g. 0.0763 means 7.63%.",
        "missing_values": "Left blank; each blank has a row in the 'missing' table with a reason below.",
    }


def _reason_rows() -> Table:
    return [[f"reason: {reason.value}", text] for reason, text in REASON_DESCRIPTIONS.items()]


def _display(value: Value, unit: Unit | None) -> Value:
    if unit == "usd" and isinstance(value, int):
        return value // 1000 if value % 1000 == 0 else value / 1000
    return value


def _write_csv(path: Path, rows: Table) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return path


def _write_xlsx(path: Path, tables: dict[str, Table]) -> Path:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name, rows in tables.items():
        sheet = workbook.create_sheet(sheet_name)
        for row in rows:
            sheet.append(row)
        for cells in sheet.iter_rows():
            for cell in cells:
                if cell.data_type == "f":  # text from a filing that starts with "="
                    cell.data_type = "s"
        sheet.freeze_panes = "A2"
    workbook.save(path)
    return path
