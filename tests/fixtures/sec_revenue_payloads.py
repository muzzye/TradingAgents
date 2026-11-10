"""Frozen SEC/XBRL payloads for the revenue-semantics regression (§2 of P2).

Every fact here is a constant copied from the shape of the real SEC
``companyfacts`` payload, not computed by the modules under test. The values
come from Berkshire Hathaway's Q3 2026 10-Q (CIK 0001067983) as the data
lineage audit recorded them:

- ``Revenues`` (consolidated) ``105,647,000,000`` for Q3 2026 and
  ``94,972,000,000`` for Q3 2025, so the year-over-year change is
  ``(105,647 - 94,972) / 94,972 = +11.24%``.
- ``RevenueFromContractWithCustomerExcludingAssessedTax`` (a *component*)
  ``74,322,000,000`` for the same quarter.

The payloads below isolate one hazard each, so a failure names its cause. They
are not the production payload: the real one is cached on the machine that
fetched it and is exercised separately by ``test_p2_financial_data_integrity``
when present.

A fact in ``companyfacts`` is a flat record::

    {"start": ..., "end": ..., "val": ..., "accn": ..., "fy": ..., "fp": ...,
     "form": "10-Q", "filed": "2026-10-25", "frame": ...}

Duration facts (revenue) carry ``start``; instant facts (assets) do not.
"""

from __future__ import annotations

CIK = "0001067983"
ACCESSION = "0001067983-26-000042"
ANALYSIS_DATE = "2026-11-09"

CONSOLIDATED = "Revenues"
CONTRACT = "RevenueFromContractWithCustomerExcludingAssessedTax"
CONTRACT_INCL = "RevenueFromContractWithCustomerIncludingAssessedTax"
SALES_NET = "SalesRevenueNet"
SEGMENT = "SegmentReportingInformationRevenue"
NONCONTRACT = "RevenueNotFromContractWithCustomer"

Q3_2026 = ("2026-07-01", "2026-09-30")
Q3_2025 = ("2025-07-01", "2025-09-30")
YTD_2026 = ("2026-01-01", "2026-09-30")
YTD_2025 = ("2025-01-01", "2025-09-30")

REVENUE_Q3_2026 = 105_647_000_000.0
REVENUE_Q3_2025 = 94_972_000_000.0
REVENUE_Q3_2026_CONTRACT = 74_322_000_000.0
REVENUE_YTD_2026 = 301_212_000_000.0
REVENUE_YTD_2025 = 273_100_000_000.0

# (105,647 / 94,972 - 1) * 100, computed by hand and written down independently.
EXPECTED_GROWTH_PCT = 11.24


def fact(value: float, *, start: str, end: str, filed: str = "2026-10-25",
         form: str = "10-Q", accn: str = ACCESSION) -> dict:
    """One duration fact, shaped as ``companyfacts`` states it."""
    return {"start": start, "end": end, "val": value, "filed": filed,
            "form": form, "accn": accn}


def tag(*facts: dict) -> dict:
    """A concept entry with a single USD unit, as the payload nests it."""
    return {"label": "dummy", "units": {"USD": list(facts)}}


# --- The healthy payload: both scopes present, both periods complete ---------

BOTH_SCOPES = {
    CONSOLIDATED: tag(
        fact(REVENUE_Q3_2026, start=Q3_2026[0], end=Q3_2026[1]),
        fact(REVENUE_Q3_2025, start=Q3_2025[0], end=Q3_2025[1]),
    ),
    CONTRACT: tag(
        fact(REVENUE_Q3_2026_CONTRACT, start=Q3_2026[0], end=Q3_2026[1]),
    ),
}

# --- The F1 payload: only the component is filed ----------------------------
# A filer that reports contract revenue and no consolidated total. The
# consolidated request must come back missing, never the 74,322 subset.

ONLY_CONTRACT = {
    CONTRACT: tag(
        fact(REVENUE_Q3_2026_CONTRACT, start=Q3_2026[0], end=Q3_2026[1]),
    ),
}

# --- The gap hazard: consolidated for the new quarter, component for the old -
# Before the scope-coherence rule this produced a row whose two periods measured
# different things, and a 76.08% growth against a consolidated history of 11.24%.

CONSOLIDATED_NEW_QUARTER_ONLY = {
    CONSOLIDATED: tag(
        fact(REVENUE_Q3_2026, start=Q3_2026[0], end=Q3_2026[1]),
    ),
    CONTRACT: tag(
        fact(REVENUE_Q3_2026_CONTRACT, start=Q3_2026[0], end=Q3_2026[1]),
        fact(60_000_000_000.0, start=Q3_2025[0], end=Q3_2025[1]),
    ),
}

# --- Alternative spellings and an unexpected taxonomy -----------------------
# A filer that renamed its revenue line over the years: the annual report used
# `SalesRevenueNet`, the latest 10-Q `Revenues`. Both are consolidated, so both
# may fill the row; `SomethingNovel` is UNKNOWN and must not.

RENAMED_OVER_TIME = {
    SALES_NET: tag(
        fact(REVENUE_Q3_2025, start=Q3_2025[0], end=Q3_2025[1],
             filed="2025-10-28"),
    ),
    CONSOLIDATED: tag(
        fact(REVENUE_Q3_2026, start=Q3_2026[0], end=Q3_2026[1]),
    ),
}

UNKNOWN_TAXONOMY = {
    "RevenuesFromOperationsAndOtherStuff": tag(
        fact(REVENUE_Q3_2026, start=Q3_2026[0], end=Q3_2026[1]),
    ),
}

# --- Quarterly vs year-to-date in one filing -------------------------------
# The same 10-Q states the quarter and the nine months to date under the same
# end date. A quarterly request must take the quarter, not the nine months.

QUARTER_AND_YTD = {
    CONSOLIDATED: tag(
        fact(REVENUE_Q3_2026, start=Q3_2026[0], end=Q3_2026[1]),
        fact(REVENUE_YTD_2026, start=YTD_2026[0], end=YTD_2026[1]),
    ),
}

# --- Duplicated facts in one filing, and a later amendment -----------------
# The same period filed twice (a 10-Q and a 10-Q/A). The later filing wins from
# the day it was filed, and not before.

DUPLICATED_FACTS = {
    CONSOLIDATED: tag(
        fact(100_000_000_000.0, start=Q3_2026[0], end=Q3_2026[1],
             filed="2026-10-25", form="10-Q", accn="0001067983-26-000042"),
        fact(REVENUE_Q3_2026, start=Q3_2026[0], end=Q3_2026[1],
             filed="2026-10-25", form="10-Q", accn="0001067983-26-000043"),
    ),
}

AMENDED_LATER = {
    CONSOLIDATED: tag(
        fact(100_000_000_000.0, start=Q3_2026[0], end=Q3_2026[1],
             filed="2026-10-25", form="10-Q", accn="orig"),
        fact(REVENUE_Q3_2026, start=Q3_2026[0], end=Q3_2026[1],
             filed="2026-11-05", form="10-Q/A", accn="amend"),
    ),
}

# --- Segment-only revenue --------------------------------------------------
# A dimensioned fact is a slice whatever its tag; the payload shape here is the
# one `select_by_scope` guards, since `companyfacts` flattens axes.

SEGMENT_ONLY = {
    SEGMENT: tag(
        fact(50_000_000_000.0, start=Q3_2026[0], end=Q3_2026[1]),
    ),
}

# --- Missing entirely ------------------------------------------------------

EMPTY: dict = {}
