"""Valuation reconciliation: per-share math with consistent dates and classes.

The BRK-B report computed P/B against a class-A book value with a class-B
price — a ratio wrong by 1500x — and paired a market cap with a share count
from a different vintage. The validator's primitives
(``convert_share_class``, ``validate_share_class_comparison``,
``reconcile_metric``, ``validate_currency_consistency``) already refuse each
hazard; this module composes them into one deterministic computation of the
three figures a valuation claim rests on:

- **book value per share** — equity / shares, both from one statement period;
- **market cap** — price x shares, price and shares from the same as-of date;
- **price-to-book** — price / BVPS, only after the share classes match.

Every input carries its class and currency; a mismatch is a typed issue, not
a silently wrong ratio. Nothing here is per-ticker: the share-class ratio
comes from the caller (the vendor or a verified source), never from prices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from tradingagents.dataflows.validator import (
    ValidationResult,
    convert_share_class,
    validate_currency_consistency,
    validate_share_class_comparison,
)


@dataclass(frozen=True)
class EquityInputs:
    """The raw inputs a valuation needs, each with its provenance labels."""

    price: float | None
    price_class: str | None          # "A"/"B"/None when single-class
    price_currency: str | None
    equity: float | None             # total stockholders' equity, base units
    equity_currency: str | None
    shares_outstanding: float | None
    bvps_reported: float | None      # vendor-reported book value per share, optional
    bvps_class: str | None           # the class the reported BVPS is per share of
    bvps_currency: str | None
    share_class_ratio: float | None  # 1 <from> = ratio <to>; None when single-class


@dataclass
class ValuationResult:
    """The reconciled figures, each None when its inputs did not reconcile."""

    book_value_per_share: float | None = None
    market_cap: float | None = None
    price_to_book: float | None = None
    issues: ValidationResult = field(default_factory=ValidationResult)

    @property
    def is_usable(self) -> bool:
        """Whether the figures may feed a decision: no error-severity issue."""
        return not self.issues.has_errors

    def summary(self) -> dict:
        return {
            "book_value_per_share": self.book_value_per_share,
            "market_cap": self.market_cap,
            "price_to_book": self.price_to_book,
            "status": self.issues.status,
            "issues": [
                {"code": i.code, "severity": i.severity, "message": i.message}
                for i in self.issues.issues
            ],
        }


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def reconcile_valuation(inputs: EquityInputs) -> ValuationResult:
    """Compute BVPS, market cap and P/B with every consistency check applied.

    Rules, in order:

    - Currency: the price and the equity must be comparable. A minor-unit
      quote (GBp) against main-unit financials (GBP) passes with a warning
      and the conversion applied; a real mismatch is an error and the
      per-share math is not computed.
    - Share class: a reported BVPS per a different class than the price is
      an error unless converted first; this module converts when a ratio is
      given, and flags when it is not.
    - BVPS is derived from equity/shares when both exist; a vendor-reported
      BVPS is reconciled against the derived one at a 1% materiality.
    - Market cap is price x shares — never price x a share count from a
      different date (the caller supplies one consistent count).
    - P/B is computed only when both sides exist in the same class.
    """
    result = ValuationResult()
    price, equity, shares = inputs.price, inputs.equity, inputs.shares_outstanding

    # Currency consistency before any division. Only a *known* mismatch
    # blocks; unknown currencies on either side are the validator's
    # UNVERIFIED warning, and the math may still proceed.
    if _finite(price) and inputs.price_currency:
        currency_check = validate_currency_consistency(
            inputs.price_currency, inputs.equity_currency or inputs.price_currency
        )
        result.issues.issues.extend(currency_check.issues)
        if currency_check.has_errors:
            return result
    elif _finite(price) and not inputs.price_currency:
        result.issues.add(
            "CURRENCY_UNKNOWN", "warning",
            "price currency not reported; ratios are unverified until known",
        )

    # Book value per share: derive from equity/shares when possible.
    derived_bvps: float | None = None
    if _finite(equity) and _finite(shares):
        derived_bvps = equity / shares

    reported_bvps = inputs.bvps_reported
    # The class the BVPS we will actually use is per; conversion rewrites it.
    bvps_class = inputs.bvps_class
    if reported_bvps is not None and inputs.bvps_class and inputs.price_class \
            and inputs.bvps_class.upper() != inputs.price_class.upper():
        # The reported BVPS is per another class: convert when a ratio exists,
        # refuse when it does not (DEF-006: the unconverted ratio is wrong by
        # orders of magnitude).
        if inputs.share_class_ratio and inputs.share_class_ratio > 0:
            try:
                reported_bvps = convert_share_class(
                    reported_bvps, inputs.share_class_ratio,
                    inputs.bvps_class, inputs.price_class,
                )
                bvps_class = inputs.price_class
                result.issues.add(
                    "SHARE_CLASS_CONVERTED", "warning",
                    f"book value per {inputs.bvps_class} share converted to per "
                    f"{inputs.price_class} at 1:{inputs.share_class_ratio:g} "
                    "before comparison",
                )
            except ValueError as exc:
                result.issues.add("SHARE_CLASS_CONVERSION_FAILED", "error", str(exc))
                return result
        else:
            class_check = validate_share_class_comparison(
                price or 0.0, reported_bvps, inputs.price_class, inputs.bvps_class
            )
            result.issues.issues.extend(class_check.issues)
            return result

    # Reconcile the derived BVPS against the vendor's when both exist.
    bvps = derived_bvps if derived_bvps is not None else reported_bvps
    if derived_bvps is not None and reported_bvps is not None and reported_bvps > 0:
        delta_pct = (reported_bvps - derived_bvps) / derived_bvps * 100
        if abs(delta_pct) > 1.0:
            result.issues.add(
                "BVPS_MISMATCH", "error",
                f"reported BVPS {reported_bvps:,.4f} vs derived {derived_bvps:,.4f} "
                f"({delta_pct:+.2f}%, beyond 1% materiality)",
                expected=derived_bvps, actual=reported_bvps,
            )
        elif delta_pct != 0:
            result.issues.add(
                "BVPS_MINOR_DRIFT", "warning",
                f"reported BVPS differs from derived by {delta_pct:+.4f}%",
            )
    result.book_value_per_share = bvps

    # Market cap: price x shares, one consistent count.
    if _finite(price) and _finite(shares):
        result.market_cap = price * shares

    # P/B: only when both sides exist and the classes already match.
    if _finite(price) and bvps is not None and bvps > 0:
        if inputs.price_class and bvps_class:
            class_check = validate_share_class_comparison(
                price, bvps, inputs.price_class, bvps_class
            )
            # After conversion the classes agree; a residual mismatch is an error.
            if class_check.has_errors:
                result.issues.issues.extend(class_check.issues)
                return result
        result.price_to_book = price / bvps

    return result
