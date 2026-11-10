"""The certified figures a report states, built from MetricRecord (§3 of P4).

A report's numbers used to arrive two ways: the engine computed them, and the
model wrote its own beside them. Nothing distinguished the two, so a table in a
report could quote 1.7x while the engine's registry said 1.6041x — and both read
as "the P/B of this analysis".

This module renders the tables **from the registry**, never from prose. A metric
is shown with its value, its formula, its inputs and its status, so a reader can
see which figures were computed and which were refused. A model's claim appears
only when it disagrees with a computed value, and then it is labelled as the
claim it was.

Rendering is deliberately dumb: it formats what a ``MetricRecord`` already says
and adds no numbers of its own.
"""

from __future__ import annotations

from typing import Any

from tradingagents.dataflows.math.metric_record import (
    CalculationStatus,
    MetricRecord,
    SourceStatus,
)

# How a reader should read each status in a table. Kept beside the enum so a
# new status cannot be added without deciding what it means in a report.
_STATUS_LABELS: dict[CalculationStatus, str] = {
    CalculationStatus.VERIFIED: "certified",
    CalculationStatus.MISMATCH: "contradicted the report",
    CalculationStatus.NOT_COMPUTABLE: "not computable from this run's inputs",
    CalculationStatus.REFUSED: "refused: the inputs did not reconcile",
    CalculationStatus.UNSUPPORTED: "unverified: no denominator to check against",
}


def _fmt(record: MetricRecord) -> str:
    """The value as the report shows it, or a dash when there is none."""
    if record.value is None:
        return "—"
    return record.display


def render_metric_table(registry: Any, *, title: str = "Certified metrics") -> str:
    """A markdown table of every metric in ``registry``, with its status.

    Every record is shown, including the refused and not-computable ones: a
    table that lists only the successes hides exactly the gaps a reader needs.
    """
    if registry is None:
        return (f"## {title}\n\nNo metric registry was built for this run, so no "
                "figure in the analyst reports could be certified.\n")

    records: list[MetricRecord] = list(registry._records.values())  # noqa: SLF001
    if not records:
        return f"## {title}\n\nThis run's registry is empty.\n"

    lines = [
        f"## {title}",
        "",
        "| Metric | Value | Unit | Formula | Period | Status |",
        "|---|---|---|---|---|---|",
    ]
    for record in sorted(records, key=lambda r: r.metric):
        status = _STATUS_LABELS.get(record.calculation_status,
                                    str(record.calculation_status))
        lines.append(
            f"| `{record.metric}` | {_fmt(record)} | {record.unit} | "
            f"`{record.formula_id}` | {record.period_end or '—'} | {status} |"
        )

    notes = [r for r in records if r.reason]
    if notes:
        lines.extend(["", "**Why a figure is not certified**", ""])
        for record in sorted(notes, key=lambda r: r.metric):
            lines.append(f"- `{record.metric}`: {record.reason}")

    claims = [r for r in records if r.llm_claimed_value is not None]
    if claims:
        lines.extend(["", "**Figures the report stated that the engine did not confirm**", ""])
        for record in sorted(claims, key=lambda r: r.metric):
            difference = (f" (difference {record.difference})"
                          if record.difference is not None else "")
            lines.append(
                f"- `{record.metric}`: the report said {record.llm_claimed_value}, "
                f"the engine computed {_fmt(record)}{difference}"
            )

    lines.append("")
    lines.append(_summary_line(records))
    return "\n".join(lines) + "\n"


def _summary_line(records: list[MetricRecord]) -> str:
    """One sentence stating how much of the run is actually certified."""
    usable = [r for r in records if r.is_usable]
    claimed = [r for r in records if r.source_status is SourceStatus.LLM_CLAIMED]
    parts = [f"{len(usable)} of {len(records)} metrics certified"]
    if claimed:
        parts.append(f"{len(claimed)} rest on the model's own figures")
    blocked = [r for r in records if r.blocks_decision]
    if blocked:
        parts.append(f"{len(blocked)} block an executable decision")
    return "*" + "; ".join(parts) + ".*"


def certified_table_markdown(final_state: dict,
                             *, title: str = "Certified metrics") -> str:
    """The certified table for a finished run, or a note that none was built.

    Reads ``final_state["metric_registry"]`` — attached by the graph when the
    registry is built — so the report and the audit show the same records.
    """
    registry = final_state.get("metric_registry")
    if registry is None:
        status = final_state.get("metric_registry_status") or {}
        note = status.get("note") or (
            "the registry was not attached to this run's state")
        return (f"## {title}\n\nNo certified metrics: {note}. Any figure quoted "
                "in the agent sections below came from a model, not an engine "
                "computation.\n")
    return render_metric_table(registry, title=title)


def append_certified_table(report_markdown: str, final_state: dict,
                           *, title: str = "Certified metrics") -> str:
    """Insert the certified table into a finished report, before its appendix.

    The table goes near the top of the document, after the header, so a reader
    meets the certified numbers before the prose that discusses them.
    """
    table = certified_table_markdown(final_state, title=title)
    if not report_markdown:
        return table
    marker = "\n\n## "
    index = report_markdown.find(marker)
    if index == -1:
        return report_markdown.rstrip("\n") + "\n\n" + table
    return report_markdown[:index + 2] + table + report_markdown[index + 2:]
