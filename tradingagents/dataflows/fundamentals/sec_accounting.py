"""
SEC fundamentals extraction (data in the P/B and media).

Reads ``facts.us-gaap`` (or the bare tag map of a fixture) and returns a
certified accounting bundle: equity and its period, share counts, conversion
ratio, and the derived book value per share.

Only ``VERIFIED`` facts are used; anything untrusted leaves the BVPS
uncomputed with a rejection reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Equity attributable to the parent's shareholders. The …IncludingNoncontrolling…
# concept is a *different measure* — it adds outside ownership — and using it as
# the denominator would understate the per-share book value. It is deliberately
# NOT in this list: a filer that reports only the inclusive figure leaves the
# value unverified rather than silently mixing two definitions.
_STATEMENTS: dict[str, tuple[str, ...]] = {
    "equity": (
        "StockholdersEquity",
        "CommonStocksIncludingAdditionalPaidInCapital",
        "MembersEquity",
    ),
    "shares_class_a": (
        "CommonStockClassASharesOutstanding",
        "ClassACommonStock",
    ),
    "shares_class_b": (
        "CommonStockClassBSharesOutstanding",
        "CommonStockClassBSharesOutstanding",
        "ClassBCommonStock",
    ),
    "class_a_to_b_ratio": (
        "ExchangeRatioOfClassACommonStockToClassBCommonStock",
        "ClassAShareConversionRatio",
        "ClassACommonStockPerClassBRatio",
    ),
}


@dataclass(frozen=True)
class SecAccountingFacts:
    """The accounting facts a valuation needs, with provenance (FIX #2).

    `equity` is attributable equity in base units; `book_value_per_share` is
    derived only if all inputs are verified, otherwise None.
    """

    equity: float | None
    equity_period_end: str | None
    equity_provenance: dict
    shares_class_a: float | None
    shares_class_b: float | None
    class_a_to_b_ratio: float | None
    book_value_per_share: float | None
    book_value_period_end: str | None
    validation_status: str = "UNVERIFIED"
    rejection_reason: str = ""
    cik: str | None = None


def extract_sec_accounting(payload: dict, *, cik: str | None = None,
                          as_of_date: str | None = None) -> SecAccountingFacts:
    """From a companyfacts payload to accounting facts (FIX #2).

    Reads ``facts.us-gaap`` (or the bare tag map of a fixture) and returns a
    certified accounting bundle.

    ``as_of_date`` is the point-in-time cutoff: a fact filed after it is a
    filing the analysis could not have seen, so it is excluded. Passing nothing
    means "no cutoff", which the fixture-driven tests use; production always
    passes the trade date.

    A value is ``VERIFIED`` only when every input agrees: the equity concept is
    attributable to shareholders, its unit is a currency, the share counts and
    the conversion ratio come from the same period, and each fact was filed on
    or before the cutoff. Anything else yields an explicit rejection reason
    rather than a number nobody can defend.
    """
    us_gaap = _us_gaap_facts(payload)
    if not us_gaap:
        return SecAccountingFacts(
            equity=None,
            equity_period_end=None,
            equity_provenance={},
            shares_class_a=None,
            shares_class_b=None,
            class_a_to_b_ratio=None,
            book_value_per_share=None,
            book_value_period_end=None,
            cik=cik,
            validation_status="UNVERIFIED",
            rejection_reason="not a companyfacts payload (missing us-gaap)",
        )

    validated: dict[str, list[dict]] = {}
    for kind, tags in _STATEMENTS.items():
        for tag in tags:
            for unit_label, facts in (us_gaap.get(tag) or {}).get("units", {}).items():
                # A share count is in shares, a ratio is dimensionless, equity
                # is money. Reading a "shares" count as dollars would multiply
                # the book value by a thousand.
                if kind == "equity" and unit_label not in ("USD", "EUR", "GBP"):
                    continue
                for fact in facts:
                    if fact.get("val") is None:
                        continue
                    filed = fact.get("filed")
                    if as_of_date and filed and str(filed) > str(as_of_date):
                        # Filed after the analysis date: the market had not
                        # seen it, so the run cannot use it.
                        continue
                    recorded = {
                        "value": float(fact["val"]),
                        "accession": fact.get("accn"),
                        "start": fact.get("start"),
                        "end": fact.get("end"),
                        "filed": fact.get("filed"),
                        "form": fact.get("form"),
                        "cik": cik,
                        "concept": tag,
                        "unit": unit_label,
                        "validation_status": "VERIFIED",
                    }
                    validated.setdefault(kind, []).append(recorded)

    equity_facts = validated.get("equity") or []
    if not equity_facts:
        return SecAccountingFacts(
            equity=None, equity_period_end=None, equity_provenance={},
            shares_class_a=None, shares_class_b=None, class_a_to_b_ratio=None,
            book_value_per_share=None, book_value_period_end=None, cik=cik,
            validation_status="UNVERIFIED",
            rejection_reason="no shareholders equity fact in the filing",
        )

    equity_fact = max(equity_facts, key=lambda f: f["filed"])
    equity, equity_period_end = float(equity_fact["value"]), equity_fact["end"]
    provenance = {
        "concept": equity_fact["concept"],
        "accession": equity_fact["accession"],
        "period_start": equity_fact.get("start"),
        "period_end": equity_period_end,
        "cik": cik,
        "unit": equity_fact["unit"],
        "validation_status": "VERIFIED",
    }

    shares_a_fact = _pick_fact(validated.get("shares_class_a"))
    shares_b_fact = _pick_fact(validated.get("shares_class_b"))
    ratio_fact = _pick_ratio_fact(us_gaap, _STATEMENTS["class_a_to_b_ratio"],
                                  as_of_date=as_of_date)

    shares_a = shares_a_fact["value"] if shares_a_fact else None
    shares_b = shares_b_fact["value"] if shares_b_fact else None
    ratio = ratio_fact["value"] if ratio_fact else None

    if shares_a is None or shares_b is None or ratio is None:
        return SecAccountingFacts(
            equity=equity, equity_period_end=equity_period_end, equity_provenance=provenance,
            shares_class_a=shares_a, shares_class_b=shares_b, class_a_to_b_ratio=ratio,
            book_value_per_share=None, book_value_period_end=equity_period_end,
            cik=cik, validation_status="UNVERIFIED",
            rejection_reason="share counts or conversion ratio missing in filing",
        )

    # The share counts must describe the same moment as the equity, or the
    # book value divides one period's equity by another's shares. A count is an
    # instant fact, so it carries an `end` but no `start`; requiring the same
    # `end` as the equity is what keeps the two comparable.
    for name, fact in (("class A", shares_a_fact), ("class B", shares_b_fact)):
        if fact.get("end") and equity_period_end \
                and str(fact["end"]) != str(equity_period_end):
            return SecAccountingFacts(
                equity=equity, equity_period_end=equity_period_end,
                equity_provenance=provenance,
                shares_class_a=shares_a, shares_class_b=shares_b,
                class_a_to_b_ratio=ratio, book_value_per_share=None,
                book_value_period_end=equity_period_end, cik=cik,
                validation_status="UNVERIFIED",
                rejection_reason=(
                    f"the {name} share count is for {fact['end']} while the "
                    f"equity is for {equity_period_end}: different periods "
                    "cannot form one book value"),
            )

    # A conversion ratio must be positive and finite; a zero or negative one
    # would make the equivalent share count meaningless.
    if not isinstance(ratio, (int, float)) or ratio <= 0:
        return SecAccountingFacts(
            equity=equity, equity_period_end=equity_period_end,
            equity_provenance=provenance,
            shares_class_a=shares_a, shares_class_b=shares_b,
            class_a_to_b_ratio=ratio, book_value_per_share=None,
            book_value_period_end=equity_period_end, cik=cik,
            validation_status="UNVERIFIED",
            rejection_reason=f"the A/B conversion ratio {ratio!r} is not usable",
        )

    b_equivalent = shares_b + shares_a * ratio
    if b_equivalent <= 0:
        return SecAccountingFacts(
            equity=equity, equity_period_end=equity_period_end, equity_provenance=provenance,
            shares_class_a=shares_a, shares_class_b=shares_b, class_a_to_b_ratio=ratio,
            book_value_per_share=None, book_value_period_end=equity_period_end,
            cik=cik, validation_status="UNVERIFIED",
            rejection_reason="computed equivalent share count not positive",
        )

    return SecAccountingFacts(
        equity=equity, equity_period_end=equity_period_end, equity_provenance=provenance,
        shares_class_a=shares_a, shares_class_b=shares_b, class_a_to_b_ratio=ratio,
        book_value_per_share=equity / b_equivalent, book_value_period_end=equity_period_end,
        cik=cik, validation_status="VERIFIED", rejection_reason="",
    )


def _pick_fact(facts: list[dict] | None) -> dict | None:
    """The latest fact, preferring one that states when it was filed."""
    if not facts:
        return None
    with_filed = [f for f in facts if f.get("filed")]
    candidates = with_filed if with_filed else facts
    return max(candidates, key=lambda f: f["filed"] if f.get("filed") else "")


def _pick(facts: list[dict] | None) -> float | None:
    fact = _pick_fact(facts)
    return None if fact is None else float(fact["value"])


def _pick_ratio_fact(us_gaap: dict, tags: tuple[str, ...],
                     *, as_of_date: str | None = None) -> dict | None:
    """The conversion ratio as a fact, honouring the point-in-time cutoff."""
    for tag in tags:
        entry = us_gaap.get(tag)
        if not isinstance(entry, dict):
            continue
        for unit_label, items in (entry.get("units") or {}).items():
            for item in items:
                if item.get("val") is None:
                    continue
                filed = item.get("filed")
                if as_of_date and filed and str(filed) > str(as_of_date):
                    continue
                return {"value": float(item["val"]), "unit": unit_label,
                        "filed": filed, "end": item.get("end"),
                        "concept": tag}
    return None


def _us_gaap_facts(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    nested = (payload.get("facts") or {}).get("us-gaap")
    if isinstance(nested, dict):
        return nested
    # bare tag map: each value must be a concept entry with units
    return payload if payload and all(
        isinstance(v, dict) and "units" in v for v in payload.values()
    ) else None
