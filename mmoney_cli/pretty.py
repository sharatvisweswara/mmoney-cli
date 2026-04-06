"""Pretty-print rendering for mmoney CLI output.

Two-phase pipeline:
  API dict → Formatter → RenderTable → render_table() → terminal

Each __typename maps to a Formatter via the FORMATTERS registry.
Unknown typenames fall back to DefaultFormatter (key-value expando dump).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import click

# ============================================================================
# Data structures (pure — no I/O)
# ============================================================================


@dataclass
class StyledSegment:
    """A leaf piece of text with optional terminal styling."""

    text: str
    color: str | None = None
    bold: bool = False
    dim: bool = False


@dataclass
class ExpandoLine:
    """One line printed below a table row, indented with a connector glyph."""

    segments: list[StyledSegment] = field(default_factory=list)


@dataclass
class ExpandoBlock:
    """A group of ExpandoLines belonging to one table row."""

    lines: list[ExpandoLine] = field(default_factory=list)


@dataclass
class Cell:
    """One cell in a table row."""

    value: str
    color: str | None = None
    bold: bool = False
    dim: bool = False
    max_width: int | None = None  # truncate with "…" if exceeded


@dataclass
class TableRow:
    """One row in a RenderTable plus optional expando blocks beneath it."""

    cells: list[Cell] = field(default_factory=list)
    expando: list[ExpandoBlock] = field(default_factory=list)


@dataclass
class RenderTable:
    """The complete table to be rendered."""

    headers: list[str] = field(default_factory=list)
    rows: list[TableRow] = field(default_factory=list)


# ============================================================================
# Formatter registry
# ============================================================================

FORMATTERS: dict[str, type] = {}


def register(typename: str):
    """Class decorator: register a Formatter for a GraphQL __typename."""

    def decorator(cls):
        FORMATTERS[typename] = cls
        return cls

    return decorator


class DefaultFormatter:
    """Fallback formatter — renders every non-None scalar as expando key: value."""

    headers: ClassVar[list[str]] = []

    def format(self, record: dict[str, Any]) -> TableRow:
        lines = []
        for k, v in record.items():
            if k == "__typename":
                continue
            if isinstance(v, (dict, list)) or v is None:
                continue
            lines.append(
                ExpandoLine(
                    segments=[
                        StyledSegment(f"{k}: ", bold=True),
                        StyledSegment(str(v)),
                    ]
                )
            )
        return TableRow(cells=[], expando=[ExpandoBlock(lines=lines)] if lines else [])


# ============================================================================
# TransactionRuleV2 formatter
# ============================================================================

_OPERATOR_LABEL = {
    "eq": "=",
    "contains": "contains",
    "starts_with": "starts with",
    "ends_with": "ends with",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def _op(operator: str) -> str:
    return _OPERATOR_LABEL.get(operator, operator)


@register("TransactionRuleV2")
class TransactionRuleV2Formatter:
    headers: ClassVar[list[str]] = ["#", "Criteria", "Category", "Merchant", "Review", "Other"]

    def format(self, record: dict[str, Any]) -> TableRow:
        criteria_lines = self._criteria_lines(record)

        cat = record.get("setCategoryAction")
        category_val = f"{cat.get('icon', '')} {cat['name']}".strip() if cat else "—"

        merchant = record.get("setMerchantAction")
        merchant_val = (
            (merchant["name"] if isinstance(merchant, dict) else merchant) if merchant else "—"
        )

        review = record.get("reviewStatusAction")
        review_cell = Cell(
            review if review else "—",
            color="green" if review == "reviewed" else ("yellow" if review else None),
            dim=not review,
        )

        other_lines = self._other_lines(record)

        cells = [
            Cell(str(record.get("order", "")), dim=True),
            Cell("\n".join(criteria_lines) if criteria_lines else "—", bold=True),
            Cell(category_val),
            Cell(merchant_val, dim=not merchant),
            review_cell,
            Cell("\n".join(other_lines), dim=True),
        ]
        return TableRow(cells=cells)

    def _criteria_lines(self, r: dict[str, Any]) -> list[str]:
        parts: list[str] = []

        for criterion in r.get("originalStatementCriteria") or []:
            parts.append(f'statement {_op(criterion["operator"])} "{criterion["value"]}"')

        for criterion in r.get("merchantCriteria") or []:
            parts.append(f'merchant {_op(criterion["operator"])} "{criterion["value"]}"')

        for criterion in r.get("merchantNameCriteria") or []:
            parts.append(f'merchant name {_op(criterion["operator"])} "{criterion["value"]}"')

        amt = r.get("amountCriteria")
        if amt:
            prefix = "expense" if amt.get("isExpense") else "income"
            rng = amt.get("valueRange")
            if rng:
                parts.append(f"{prefix} between {rng['lower']}–{rng['upper']}")
            else:
                parts.append(f"{prefix} {_op(amt['operator'])} {amt.get('value', '')}")

        cats = r.get("categories") or []
        if cats:
            names = ", ".join(c["name"] for c in cats)
            parts.append(f"category in [{names}]")

        accounts = r.get("accounts") or []
        if accounts:
            names = ", ".join(a["displayName"] for a in accounts)
            parts.append(f"account in [{names}]")

        owners = r.get("criteriaOwnerUsers") or []
        if owners:
            names = ", ".join(u["displayName"] for u in owners)
            parts.append(f"owner in [{names}]")
        elif r.get("criteriaOwnerIsJoint"):
            parts.append("owner: joint")

        return parts

    def _other_lines(self, r: dict[str, Any]) -> list[str]:
        parts: list[str] = []

        tags = r.get("addTagsAction") or []
        if tags:
            parts.append("• " + ", ".join(t["name"] for t in tags))

        if r.get("sendNotificationAction"):
            parts.append("• notify")

        if r.get("setHideFromReportsAction"):
            parts.append("• hide from reports")

        if r.get("needsReviewByUserAction"):
            user = r["needsReviewByUserAction"]
            parts.append("• needs review: " + user.get("displayName", user.get("id", "")))

        split = r.get("splitTransactionsAction")
        if split:
            n = len(split.get("splitsInfo") or [])
            parts.append(f"• {n} split{'s' if n != 1 else ''}")

        return parts


def _hex_to_click(hex_color: str | None) -> str | None:
    """Return a click-compatible color name for common hex values, else None."""
    if not hex_color:
        return None
    _map = {
        "#ffcb12": "yellow",
        "#ff6b6b": "red",
        "#51cf66": "green",
        "#339af0": "blue",
        "#cc5de8": "magenta",
        "#ff922b": "bright_red",
        "#20c997": "cyan",
    }
    return _map.get(hex_color.lower())


# ============================================================================
# Renderer
# ============================================================================

_EXPANDO_PREFIX = "    ┗━ "
_COL_GAP = "  "


def _truncate(text: str, max_width: int) -> str:
    if len(text) <= max_width:
        return text
    return text[: max_width - 1] + "…"


def render_table(table: RenderTable, use_color: bool = True) -> None:
    """Render a RenderTable to stdout."""
    has_headers = bool(table.headers)

    # ── 1. Compute column widths from unstyled cell values ──────────────────
    col_widths: list[int] = [len(h) for h in table.headers] if has_headers else []

    for row in table.rows:
        for i, cell in enumerate(row.cells):
            if i >= len(col_widths):
                col_widths.append(0)
            for line in cell.value.split("\n"):
                visible = len(line)
                if cell.max_width:
                    visible = min(visible, cell.max_width)
                col_widths[i] = max(col_widths[i], visible)

    total_width = max(
        sum(col_widths) + len(_COL_GAP) * (len(col_widths) - 1) if col_widths else 0,
        40,
    )

    # ── 2. Header ────────────────────────────────────────────────────────────
    if has_headers:
        header_parts = []
        for i, h in enumerate(table.headers):
            padded = h.ljust(col_widths[i])
            header_parts.append(click.style(padded, bold=True) if use_color else padded)
        click.echo(_COL_GAP.join(header_parts))
        click.echo(click.style("─" * total_width, dim=True) if use_color else "─" * total_width)

    # ── 3. Rows ───────────────────────────────────────────────────────────────
    for row in table.rows:
        if not row.cells and not row.expando:
            continue
        if not row.cells:
            # DefaultFormatter path — no table cells, only expando
            _render_expando(row.expando, use_color, indent=0)
            continue

        # Expand multi-line cell values into sub-lines
        cell_sublines: list[list[str]] = []
        for _i, cell in enumerate(row.cells):
            sublines = cell.value.split("\n")
            if cell.max_width:
                sublines = [_truncate(s, cell.max_width) for s in sublines]
            cell_sublines.append(sublines)

        max_sublines = max((len(sl) for sl in cell_sublines), default=1)

        for sl_idx in range(max_sublines):
            parts = []
            for i, sublines in enumerate(cell_sublines):
                text = sublines[sl_idx] if sl_idx < len(sublines) else ""
                padded = text.ljust(col_widths[i] if i < len(col_widths) else 0)
                if use_color:
                    cell = row.cells[i]
                    padded = click.style(
                        padded,
                        fg=cell.color if sl_idx == 0 else None,
                        bold=cell.bold and sl_idx == 0,
                        dim=cell.dim,
                    )
                parts.append(padded)
            click.echo(_COL_GAP.join(parts))

        # Indent expando to align with the last column
        if col_widths:
            expando_indent = sum(col_widths[:-1]) + len(_COL_GAP) * (len(col_widths) - 1)
        else:
            expando_indent = 0
        _render_expando(row.expando, use_color, expando_indent)

    # ── 4. Footer ─────────────────────────────────────────────────────────────
    if has_headers:
        click.echo(click.style("─" * total_width, dim=True) if use_color else "─" * total_width)


def _render_expando(blocks: list[ExpandoBlock], use_color: bool, indent: int = 0) -> None:
    prefix = " " * indent + "┗━ "
    for block in blocks:
        for line in block.lines:
            rendered_segs = []
            for seg in line.segments:
                if use_color:
                    rendered_segs.append(
                        click.style(seg.text, fg=seg.color, bold=seg.bold, dim=seg.dim)
                    )
                else:
                    rendered_segs.append(seg.text)
            click.echo(prefix + "".join(rendered_segs))


# ============================================================================
# _extract_records — duplicated from cli.py to avoid circular imports
# ============================================================================

_LIST_KEYS = [
    "accounts",
    "results",
    "transactions",
    "categories",
    "householdTransactionTags",
    "credentials",
    "budgetData",
    "recurringTransactions",
    "transactionRules",
    "splits",
    "snapshots",
    "history",
]


def _extract_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return list(data)
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict) and "results" in value:
                results = value["results"]
                return list(results) if isinstance(results, list) else []
            if key in _LIST_KEYS and isinstance(value, list):
                return list(value)
        return [dict(data)]
    return []


# ============================================================================
# Public entry point
# ============================================================================


def output_pretty(data: Any, use_color: bool = True) -> None:
    """Render data to the terminal using type-aware pretty formatting."""
    records = _extract_records(data)
    if not records:
        return

    typename: str | None = None
    first = records[0]
    if isinstance(first, dict):
        typename = first.get("__typename")

    formatter_cls = FORMATTERS.get(typename) if typename else None
    formatter = formatter_cls() if formatter_cls else DefaultFormatter()

    rows = [formatter.format(r) for r in records if isinstance(r, dict)]
    table = RenderTable(headers=list(formatter.headers), rows=rows)
    render_table(table, use_color=use_color)
