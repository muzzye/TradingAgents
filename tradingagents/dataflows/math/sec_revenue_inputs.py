"""Revenue inputs from the SEC filing, for the registry (FIX #1).

`consolidated_revenue` was populated **only** by `math/extraction.py`, which
reads a labelled sentence out of the fundamentals report with a regex. The
structured SEC facts the Fundamentals Analyst fetched — `Revenues` with its
accession, period and unit — never reached the registry. The metric was
therefore correct only when the model happened to word the figure the way the
regex expects, and `NOT_COMPUTABLE` otherwise. The 2026-11-10 16:15 report hit
exactly that: it stated 105,647 million and the table said
"no consolidated revenue was filed or reported".

This module is the missing link. It takes the `companyfacts` payload the SEC
provider already returns, selects the **consolidated** concept through the same
`concept_resolution` ordering the statement writer uses, and returns `RunInputs`
carrying both the value and the provenance the registry records.

Two rules shape it:

**Only a consolidated fact qualifies.** A component (`RevenueFromContract...`)
is what the audit's F1 finding was about; it is dropped here, not renamed.

**A missing source is `None`, not zero.** A payload with no usable period
returns `None` so the registry records `NOT_COMPUTABLE`, which is the honest
answer — never an invented figure.
"""

from __future__ import annotations

from typing import Any

from tradingagents.dataflows.math.run_registry import RunInputs

# The spans a quarterly figure occupies, as the statement writer defines them:
# one quarter is about 90 days, and a year to date is 150–290. A revenue fact
# that covers neither is not the period the analysis asks about.
_QUARTER_SPAN = (80, 100)


def revenue_inputs_from_sec(
    companyfacts: dict | None,
    as_of_date: str,
    *,
    cik: str | None = None,
    quarters_back: int = 4,
    spans: tuple[tuple[int, int], ...] = (_QUARTER_SPAN,),
) -> RunInputs | None:
    """`RunInputs` carrying the consolidated revenue the filing states.

    ``quarters_back`` is how many prior quarters to look for the comparative
    figure: the growth needs the same quarter a year earlier, so the default of
    4 covers the trailing year.

    Returns ``None`` when the payload yields no consolidated period at all: the
    caller then builds a registry with no revenue, which is `NOT_COMPUTABLE` —
    the same answer a provider outage should give.
    """
    facts = _us_gaap(companyfacts)
    if not facts:
        return None

    from tradingagents.dataflows.vendors import sec_edgar

    # The tags the income statement uses for revenue. `_as_of_with_provenance`
    # orders them by meaning (consolidated first) and refuses to mix scopes
    # within one row, so a component cannot fill a consolidated period here.
    tags = (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    )
    try:
        values, unit, provenance = sec_edgar._as_of_with_provenance(
            facts, tags, as_of_date, spans, cik=cik)
    except Exception:  # noqa: BLE001 — an unreadable payload certifies nothing
        return None

    if not values:
        return None

    # The latest period is the one the analysis is about; the comparative is
    # the same quarter a year earlier, which the key ordering gives us because
    # `_as_of_with_provenance` returns periods sorted by end date.
    keys = sorted(values)
    latest_key = keys[-1]
    current = values[latest_key]
    latest_provenance = provenance.get(latest_key)

    # Only a consolidated, validated fact may become the metric.
    if latest_provenance is None:
        return None
    if str(latest_provenance.scope) != "consolidated":
        return None
    if latest_provenance.validation_status != "VERIFIED":
        return None

    if unit != "USD":
        # A per-share or non-currency fact cannot be the revenue total.
        return None

    prior = _comparative(values, latest_key, quarters_back)

    return RunInputs(
        revenue_consolidated=float(current),
        revenue_prior=float(prior) if prior is not None else None,
        revenue_provenance=latest_provenance.summary(),
        # The value is a filed fact, so it is not the model's assertion: it is
        # deliberately absent from `claimed_inputs`.
    )


def _us_gaap(companyfacts: Any) -> dict | None:
    """The `us-gaap` facts section of a payload.

    Accepts both shapes the codebase produces: the full ``companyfacts``
    document the provider returns (``{"facts": {"us-gaap": ...}}``), and the
    bare tag map the frozen fixtures hold. Detecting by structure rather than
    by a flag keeps one caller from having to know which it was given.
    """
    if not isinstance(companyfacts, dict):
        return None
    nested = (companyfacts.get("facts") or {}).get("us-gaap")
    if isinstance(nested, dict):
        return nested
    # A bare tag map: every value is a concept entry with `units`.
    if companyfacts and all(
            isinstance(v, dict) and "units" in v for v in companyfacts.values()):
        return companyfacts
    return None


def _comparative(values: dict, latest_key: tuple[str, int],
                 quarters_back: int) -> float | None:
    """The figure for the same quarter a year earlier, when it is in the payload.

    Looked up by the period a year before the latest one rather than by list
    position: a payload that skips a quarter must not silently compare Q3
    against Q1.
    """
    latest_end = latest_key[0]
    target_year = int(latest_end[:4]) - 1
    target_end = f"{target_year}{latest_end[4:]}"
    for key in sorted(values):
        if key[0] == target_end and key[1] == latest_key[1]:
            return values[key]
    return None
