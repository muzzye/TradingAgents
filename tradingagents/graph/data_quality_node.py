"""The graph's deterministic data-quality node.

Placed between the analysts and the research debate, this node runs the
same checks the Execution Gate applies at the end of the run — but early
enough that the Research Manager and Portfolio Manager see the findings
before they conclude. Without it, a report built on a stale filing and a
contradicted percentage is debated in full and only its *order* is blocked
at the end: the analysis stays wrong, and a repeated run reproduces the
same wrong conclusion.

The node is pure: no LLM, no network. It reads the analysts' reports from
the state, computes the verdicts, and returns both the verdicts (for the
gate to reuse, so the two stages cannot disagree) and a plain-text notice
for the managers' prompts.
"""

from __future__ import annotations

import logging

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.data_gate import precheck_data_quality
from tradingagents.dataflows.filing_freshness import used_period_end_from_report

logger = logging.getLogger(__name__)


def create_data_quality_check(filings_provider=None, sec_facts_provider=None,
                              price_provider=None):
    """Build the node. ``filings_provider`` is injectable for tests.

    The default provider reads the filings known at the trade date from SEC
    EDGAR through the freshness module — point-in-time, no look-ahead. A
    provider that raises yields no filings, which the freshness check
    reports as UNAVAILABLE rather than a pass.

    ``sec_facts_provider`` supplies the raw ``companyfacts`` payload used for
    the §4 semantic reconciliation. It runs here, before synthesis, so a
    figure presented under the wrong accounting concept is refused before any
    analyst can build on it.
    """

    def data_quality_check_node(state) -> dict:
        ticker = state.get("company_of_interest", "")
        trade_date = str(state.get("trade_date", ""))

        config = get_config()
        filings = None
        if config.get("fundamentals_freshness_gate", True):
            filings = _resolve_filings(filings_provider, ticker, trade_date)

        used_end = used_period_end_from_report(state.get("fundamentals_report"))

        precheck = precheck_data_quality(
            trade_date=trade_date,
            filings=filings,
            used_fundamentals_period_end=used_end,
        )

        # §4 — the SEC concept reconciliation. Reported alongside the
        # freshness verdict so the audit shows whether it actually ran.
        sec_reconciliation = _reconcile_sec_concepts(
            sec_facts_provider, ticker, trade_date,
            state.get("fundamentals_report"),
        )
        sec_blocks = bool(
            sec_reconciliation and sec_reconciliation.get("status") == "BLOCKED"
        )
        sec_codes = tuple(sec_reconciliation.get("findings") and
                          [f["code"] for f in sec_reconciliation["findings"]
                           if f.get("blocks")] or ()) if sec_blocks else ()

        # FIX #2: publish the qualified market price and the accounting
        # inputs from SEC filings, so the P/B is computed from a verified
        # numerator and denominator rather than from prose.
        from tradingagents.dataflows.fundamentals import extract_sec_accounting

        accounting = None
        if sec_facts_provider is not None:
            try:
                payload = sec_facts_provider(ticker, trade_date)
                if isinstance(payload, dict):
                    accounting = extract_sec_accounting(
                        payload,
                        cik=(payload.get("cik")
                             or payload.get("facts", {}).get("cik")
                             or payload.get("cik_str")),
                    )
                    if accounting.validation_status != "VERIFIED":
                        logger.warning(
                            "SEC accounting is UNVERIFIED for %s (%s)",
                            ticker, accounting.rejection_reason,
                        )
            except Exception:  # noqa: BLE001 — provider unavailable is not tolerated
                logger.exception(
                    "SEC accounting extraction failed for %s", ticker)

        # FIX #2 (A): the qualified price, from the market provider — never
        # from the model's prose. `price_provider` is injectable so a test can
        # supply a frozen bar; the default reads the same OHLCV the analyst's
        # snapshot tool reads, and classifies the bar's finality.
        market_price = _qualified_price_from_provider(
            price_provider, ticker, trade_date)

        # FIX #1: the consolidated revenue the filing states, as structured
        # inputs for the registry. Without this the metric was populated only by
        # a regex over the analyst's prose, so a correctly filed figure came out
        # NOT_COMPUTABLE whenever the model worded it differently.
        revenue_inputs = _revenue_inputs_from_provider(
            sec_facts_provider, ticker, trade_date)

        logger.info(
            "Data-quality pre-check for %s on %s: blocks_execution=%s codes=%s",
            ticker, trade_date, precheck.blocks_execution or sec_blocks,
            list(precheck.blocking_codes) + list(sec_codes),
        )
        update = {
            "data_quality_precheck": {
                **precheck.summary(),
                "notice": precheck.notice(),
                # §4: a semantic mismatch blocks here, before synthesis, so no
                # analyst builds on a figure presented under the wrong concept.
                "blocks_execution": precheck.blocks_execution or sec_blocks,
                "blocking_codes": list(dict.fromkeys(
                    list(precheck.blocking_codes) + list(sec_codes))),
                "sec_reconciliation": sec_reconciliation,
            },
        }
        if revenue_inputs is not None:
            # Written into the run's state so `registry_inputs_from_state`
            # picks them up when the registry is built.
            update["revenue_consolidated"] = revenue_inputs.revenue_consolidated
            update["revenue_provenance"] = revenue_inputs.revenue_provenance
            if revenue_inputs.revenue_prior is not None:
                update["revenue_prior"] = revenue_inputs.revenue_prior

        if accounting is not None:
            for field_name, value in (
                ("equity", accounting.equity),
                ("equity_period_end", accounting.equity_period_end),
                ("shares_class_a", accounting.shares_class_a),
                ("shares_class_b", accounting.shares_class_b),
                ("class_a_to_b_ratio", accounting.class_a_to_b_ratio),
                ("book_value_per_share", accounting.book_value_per_share),
            ):
                if value is not None:
                    update[field_name] = value

        if market_price is not None:
            update["verified_market_snapshot"] = {
                "close": market_price["value"],
                "price": market_price["value"],
                "currency": market_price.get("currency"),
                "price_provenance": market_price,
            }

        return update

    return data_quality_check_node


def _qualified_price_from_provider(provider, ticker: str, trade_date: str):
    """A qualified market price for ``ticker`` as of ``trade_date``.

    The provider returns a dict with the bar's close and its finality; the
    default reads the same verified OHLCV the analyst's snapshot tool reads.
    Nothing here parses prose: a price that cannot be sourced stays absent and
    the P/B is then NOT_COMPUTABLE, which is the honest answer.
    """
    from tradingagents.dataflows.math.price_source import qualified_price

    try:
        if provider is not None:
            bar = provider(ticker, trade_date)
        else:
            bar = _default_bar(ticker, trade_date)
        if not isinstance(bar, dict):
            return None
        price = bar.get("close")
        if price is None:
            price = bar.get("price")
        if price is None:
            return None
        return qualified_price(
            ticker, float(price),
            bar_finality=bar.get("bar_finality") or bar.get("finality"),
            session_date=bar.get("session_date"),
            observation_timestamp=bar.get("timestamp")
            or bar.get("observation_timestamp"),
            source=bar.get("source") or "yfinance",
            currency=bar.get("currency"),
            share_class=bar.get("share_class"),
        )
    except Exception:  # noqa: BLE001 — an unavailable price is not a pass
        logger.debug("Could not build a qualified price for %s", ticker,
                     exc_info=True)
        return None


def _default_bar(ticker: str, trade_date: str) -> dict | None:
    """The latest verified OHLCV row, with the bar's finality.

    Reads through `snapshot._verified_rows`, the same point-in-time path the
    analyst's snapshot tool uses, so the price the engine certifies and the
    price the analyst reads come from one source.
    """
    from datetime import UTC, datetime

    from tradingagents.dataflows.vendors.bar_finality import assess_finality
    from tradingagents.dataflows.vendors.yahoo.snapshot import _verified_rows

    rows = _verified_rows(ticker, trade_date)
    if rows is None or rows.empty:
        return None
    latest = rows.iloc[-1]
    session_date = str(latest["Date"])[:10]
    finality = assess_finality(
        datetime.now(UTC).isoformat(), session_date=session_date)
    return {
        "close": float(latest["Close"]),
        "session_date": session_date,
        "bar_finality": str(finality.finality),
        "timestamp": finality.observation_timestamp,
        "source": "yfinance",
    }


def _reconcile_sec_concepts(provider, ticker: str, trade_date: str,
                            fundamentals_report: str | None) -> dict | None:
    """Reconcile the revenue the report states against the filing's concepts.

    Returns ``None`` when no payload is available: an unattempted check is not
    a pass, and inventing a verdict would be worse than saying nothing ran.
    """
    if provider is None:
        return None
    try:
        payload = provider(ticker, trade_date)
    except Exception:  # noqa: BLE001 — an unavailable source is not a pass
        logger.debug("SEC facts provider failed for %s", ticker, exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None

    from tradingagents.dataflows.math.sec_reconciliation import (
        reconcile_revenue,
        sec_facts_from_companyfacts,
    )

    concepts = (
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SegmentReportingInformationRevenue",
    )
    facts = []
    for concept in concepts:
        facts.extend(sec_facts_from_companyfacts(payload, concept))
    if not facts:
        return None

    reported = _revenue_from_report(fundamentals_report)
    if reported is None:
        return None

    verdict = reconcile_revenue(reported, facts, claimed_as_consolidated=True)
    return verdict.summary()


def _revenue_from_report(report: str | None) -> float | None:
    """The revenue figure a fundamentals report states, in base units.

    Reads only an explicitly labelled revenue line, and converts a stated
    millions/billions scale. A figure without a label is not guessed: an
    unattributable number cannot be reconciled, and pretending otherwise
    would certify something nobody identified.
    """
    if not report:
        return None
    import re

    pattern = re.compile(
        r"(?:total\s+revenue|revenues?)\D{0,24}?(\d[\d,\.]*)\s*"
        r"(million|billion|mn|bn|m|b)?",
        re.IGNORECASE,
    )
    match = pattern.search(report)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    scale = (match.group(2) or "").lower()
    if scale in ("billion", "bn", "b"):
        value *= 1_000_000_000
    elif scale in ("million", "mn", "m"):
        value *= 1_000_000
    return value


def _revenue_inputs_from_provider(provider, ticker: str, trade_date: str):
    """The filing's consolidated revenue, or ``None``.

    ``provider`` returns the raw ``companyfacts`` payload. When the node was
    built without one, the default reads it from SEC EDGAR exactly as the
    freshness check does — the same cached fetch, the same point-in-time rules.
    An unavailable source yields ``None``, which leaves the metric
    ``NOT_COMPUTABLE``: a gap is reported, never filled.
    """
    from tradingagents.dataflows.math.sec_revenue_inputs import (
        revenue_inputs_from_sec,
    )

    payload = None
    cik = None
    try:
        if provider is not None:
            payload = provider(ticker, trade_date)
        else:
            from tradingagents.dataflows.vendors import sec_edgar

            cik = sec_edgar.cik_for(ticker)
            if cik is None:
                return None
            payload = sec_edgar._cached_json(
                sec_edgar._FACTS_URL.format(cik=cik), f"CIK{cik}.json")
    except Exception:  # noqa: BLE001 — an unavailable source is not a pass
        logger.debug("SEC facts unavailable for %s", ticker, exc_info=True)
        return None

    try:
        return revenue_inputs_from_sec(payload, trade_date, cik=cik)
    except Exception:  # noqa: BLE001 — an unreadable payload certifies nothing
        logger.debug("Could not read revenue from SEC for %s", ticker,
                     exc_info=True)
        return None


def _resolve_filings(provider, ticker: str, trade_date: str):
    """The filings known at the trade date, or None when unavailable."""
    if provider is not None:
        try:
            return provider(ticker, trade_date)
        except Exception:  # noqa: BLE001 — an unavailable source is not a pass
            logger.debug("Filings provider failed for %s", ticker, exc_info=True)
            return []
    try:
        from tradingagents.dataflows.filing_freshness import filings_from_sec_edgar

        return filings_from_sec_edgar(ticker, trade_date)
    except Exception:  # noqa: BLE001 — same rule as above
        logger.debug("SEC extraction failed for %s", ticker, exc_info=True)
        return []
