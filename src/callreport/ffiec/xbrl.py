"""Parse a Call Report XBRL instance document into typed facts.

Structure observed in FFIEC facsimiles (2011-2026):

* One ``instant`` context (``CI_<rssd>_<date>``) for balances and one
  ``duration`` context (``CD_<rssd>_<date>``) for year-to-date income items.
  Both name the same institution and end on the report date.
* Each fact is a root-level element named by its MDRM code, e.g.
  ``<cc:RCFD2170 contextRef=... unitRef="USD" decimals="0">``.
* ``unitRef`` is ``USD`` (whole dollars), ``PURE`` (ratios as decimal
  fractions, e.g. 0.076257 = 7.6257%), or ``NON-MONETARY`` (counts). Facts
  without a unit are booleans (``true``/``false``) or text. Empty elements
  (seen for some text items) are treated as missing.
* The form (031, 041, 051) appears in the taxonomy ``schemaRef``, e.g.
  ``.../report031/2026-03-31/v294/...``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError as XMLSyntaxError
from defusedxml.ElementTree import fromstring

from .errors import ParseError

XBRLI = "{http://www.xbrl.org/2003/instance}"
LINK = "{http://www.xbrl.org/2003/linkbase}"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
FORM_PATTERN = re.compile(r"/report(\d{3})/")

Unit = Literal["usd", "ratio", "number", "bool", "text"]
UNIT_REFS: dict[str, Unit] = {"USD": "usd", "PURE": "ratio", "NON-MONETARY": "number"}

Value = int | float | bool | str | None


@dataclass(frozen=True)
class Fact:
    mdrm: str
    value: Value
    unit: Unit


@dataclass(frozen=True)
class Filing:
    rssd_id: int
    report_date: date
    form_type: str | None
    facts: dict[str, Fact]


def parse_facsimile(data: bytes) -> Filing:
    try:
        root = fromstring(data)
    except (XMLSyntaxError, DefusedXmlException) as exc:
        raise ParseError(f"Invalid or unsafe XML: {exc}") from exc
    if root.tag != f"{XBRLI}xbrl":
        raise ParseError(f"Not an XBRL instance (root element {root.tag!r})")

    contexts = _contexts(root)
    if not contexts:
        raise ParseError("XBRL instance has no contexts")
    entities = {rssd for rssd, _ in contexts.values()}
    end_dates = {end for _, end in contexts.values()}
    if len(entities) != 1 or len(end_dates) != 1:
        raise ParseError(f"Expected one institution and one report date, got {entities} / {end_dates}")

    facts: dict[str, Fact] = {}
    for element in root:
        if "contextRef" not in element.attrib:
            continue
        if element.attrib["contextRef"] not in contexts:
            raise ParseError(f"Fact {element.tag} references an unknown context")
        fact = _fact(element)
        existing = facts.get(fact.mdrm)
        if existing is not None and existing != fact:
            raise ParseError(f"Conflicting values for {fact.mdrm}: {existing.value!r} vs {fact.value!r}")
        facts[fact.mdrm] = fact

    return Filing(
        rssd_id=entities.pop(),
        report_date=end_dates.pop(),
        form_type=_form_type(root),
        facts=facts,
    )


def _contexts(root: Element) -> dict[str, tuple[int, date]]:
    contexts = {}
    for ctx in root.iter(f"{XBRLI}context"):
        identifier = ctx.findtext(f"{XBRLI}entity/{XBRLI}identifier")
        end = ctx.findtext(f"{XBRLI}period/{XBRLI}instant") or ctx.findtext(f"{XBRLI}period/{XBRLI}endDate")
        try:
            contexts[ctx.attrib["id"]] = (int(identifier or ""), date.fromisoformat((end or "").strip()))
        except (KeyError, ValueError) as exc:
            raise ParseError(f"Malformed context {ctx.attrib.get('id')!r}: {exc}") from exc
    return contexts


def _form_type(root: Element) -> str | None:
    for ref in root.iter(f"{LINK}schemaRef"):
        match = FORM_PATTERN.search(ref.attrib.get(XLINK_HREF, ""))
        if match:
            return match.group(1)
    return None


def _fact(element: Element) -> Fact:
    mdrm = element.tag.rsplit("}", 1)[-1]
    text = (element.text or "").strip()
    unit_ref = element.attrib.get("unitRef")

    if unit_ref is not None:
        unit = UNIT_REFS.get(unit_ref)
        if unit is None:
            raise ParseError(f"{mdrm}: unknown unitRef {unit_ref!r}")
    elif text in ("true", "false"):
        unit = "bool"
    else:
        unit = "text"

    if element.attrib.get(XSI_NIL) == "true" or not text:
        return Fact(mdrm, None, unit)
    if unit == "bool":
        return Fact(mdrm, text == "true", unit)
    if unit == "text":
        return Fact(mdrm, text, unit)
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ParseError(f"{mdrm}: non-numeric value {text!r}") from exc
    if unit == "ratio":
        return Fact(mdrm, float(number), unit)
    return Fact(mdrm, int(number) if number == number.to_integral_value() else float(number), unit)
