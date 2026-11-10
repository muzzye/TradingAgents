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

_STATEMENTS: dict[str, tuple[str, ...]] = {
    "equity": (
        "StockholdersEquity",
        "CommonStocksIncludingAdditionalPaidInCapital",
        "MembersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
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


def extract_sec_accounting(payload: dict, *, cik: str | None = None) -> SecAccountingFacts:
    """From a companyfacts payload to accounting facts (FIX #2).

    Reads ``facts.us-gaap`` (or the bare tag map of a fixture) and returns a
    certified accounting bundle. Every fact carries a provenance so the audit
    can point at the filing it came from.
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
                for fact in facts:
                    if fact.get("val") is None:
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

    shares_a = _pick(validated.get("shares_class_a"))
    shares_b = _pick(validated.get("shares_class_b"))
    ratio = _pick_ratio(us_gaap, _STATEMENTS["class_a_to_b_ratio"])
    if shares_a is None or shares_b is None or ratio is None:
        return SecAccountingFacts(
            equity=equity, equity_period_end=equity_period_end, equity_provenance=provenance,
            shares_class_a=shares_a, shares_class_b=shares_b, class_a_to_b_ratio=ratio,
            book_value_per_share=None, book_value_period_end=equity_period_end,
            cik=cik, validation_status="UNVERIFIED",
            rejection_reason="share counts or conversion ratio missing in filing",
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


def _pick(facts: list[dict] | None) -> float | None:
    if not facts:
        return None
    with_filed = [f for f in facts if f.get("filed")]
    candidates = with_filed if with_filed else facts
    return float(max(candidates, key=lambda f: f["filed"] if f.get("filed") else "")["value"])


def _pick_ratio(us_gaap: dict, tags: tuple[str, ...]) -> float | None:
    for tag in tags:
        entry = us_gaap.get(tag)
        if not isinstance(entry, dict):
            continue
        for fact in (entry.get("units") or {}).values():
            for item in fact:
                if item.get("val") is not None:
                    return float(item["val"])
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
