"""Tests for mmoney_cli/pretty.py."""

from click.testing import CliRunner

from mmoney_cli.pretty import (
    Cell,
    DefaultFormatter,
    ExpandoBlock,
    ExpandoLine,
    RenderTable,
    StyledSegment,
    TableRow,
    TransactionRuleV2Formatter,
    _extract_records,
    _truncate,
    output_pretty,
    render_table,
)

# ============================================================================
# _truncate
# ============================================================================


def test_truncate_short_string():
    assert _truncate("hello", 10) == "hello"


def test_truncate_exact():
    assert _truncate("hello", 5) == "hello"


def test_truncate_long():
    assert _truncate("hello world", 8) == "hello w…"
    assert len(_truncate("hello world", 8)) == 8


# ============================================================================
# _extract_records
# ============================================================================


def test_extract_records_list():
    data = [{"id": "1"}, {"id": "2"}]
    assert _extract_records(data) == data


def test_extract_records_known_key():
    data = {"transactionRules": [{"id": "r1"}, {"id": "r2"}]}
    assert _extract_records(data) == [{"id": "r1"}, {"id": "r2"}]


def test_extract_records_nested_results():
    data = {"allTransactions": {"results": [{"id": "t1"}], "totalCount": 1}}
    assert _extract_records(data) == [{"id": "t1"}]


def test_extract_records_single_dict():
    data = {"id": "x", "name": "foo"}
    assert _extract_records(data) == [{"id": "x", "name": "foo"}]


# ============================================================================
# DefaultFormatter
# ============================================================================


def test_default_formatter_skips_none_and_nested():
    fmt = DefaultFormatter()
    row = fmt.format(
        {"id": "abc", "name": "Foo", "nested": {"x": 1}, "tags": [1, 2], "hidden": None}
    )
    # One expando block with lines for scalar, non-None fields only
    assert len(row.expando) == 1
    keys = [seg.text.rstrip(": ") for seg in [line.segments[0] for line in row.expando[0].lines]]
    assert "id" in keys
    assert "name" in keys
    assert "nested" not in keys
    assert "tags" not in keys
    assert "hidden" not in keys


def test_default_formatter_skips_typename():
    fmt = DefaultFormatter()
    row = fmt.format({"__typename": "Foo", "id": "1"})
    keys = [line.segments[0].text.rstrip(": ") for line in row.expando[0].lines]
    assert "__typename" not in keys


def test_default_formatter_empty_record():
    fmt = DefaultFormatter()
    row = fmt.format({"__typename": "X"})
    assert row.cells == []
    assert row.expando == []


# ============================================================================
# TransactionRuleV2Formatter — criteria
# ============================================================================

_BASE_RULE: dict = {
    "__typename": "TransactionRuleV2",
    "id": "rule_001",
    "order": 0,
    "merchantCriteriaUseOriginalStatement": False,
    "merchantCriteria": None,
    "originalStatementCriteria": None,
    "merchantNameCriteria": None,
    "amountCriteria": None,
    "categories": [],
    "accounts": [],
    "criteriaOwnerIsJoint": False,
    "criteriaOwnerUserIds": None,
    "criteriaOwnerUsers": None,
    "setCategoryAction": None,
    "setMerchantAction": None,
    "addTagsAction": None,
    "reviewStatusAction": None,
    "sendNotificationAction": False,
    "setHideFromReportsAction": False,
    "needsReviewByUserAction": None,
    "splitTransactionsAction": None,
}


def _rule(**overrides):
    return {**_BASE_RULE, **overrides}


def test_rule_formatter_headers():
    assert TransactionRuleV2Formatter.headers == [
        "#",
        "Criteria",
        "Category",
        "Merchant",
        "Review",
        "Other",
    ]


def test_rule_order_in_first_cell():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(_rule(order=3))
    assert row.cells[0].value == "3"


def test_rule_original_statement_criteria():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(
        _rule(originalStatementCriteria=[{"operator": "contains", "value": "netflix"}])
    )
    assert 'statement contains "netflix"' in row.cells[1].value


def test_rule_merchant_criteria():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(_rule(merchantCriteria=[{"operator": "eq", "value": "amazon"}]))
    assert 'merchant = "amazon"' in row.cells[1].value


def test_rule_merchant_name_criteria():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(_rule(merchantNameCriteria=[{"operator": "eq", "value": "carta"}]))
    assert 'merchant name = "carta"' in row.cells[1].value


def test_rule_amount_criteria_no_range():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(
        _rule(
            amountCriteria={"operator": "gt", "isExpense": True, "value": 100, "valueRange": None}
        )
    )
    assert "expense > 100" in row.cells[1].value


def test_rule_amount_criteria_with_range():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(
        _rule(
            amountCriteria={
                "operator": "between",
                "isExpense": True,
                "value": None,
                "valueRange": {"lower": 10, "upper": 50},
            }
        )
    )
    assert "expense between 10–50" in row.cells[1].value


def test_rule_no_criteria_shows_dash():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(_rule())
    assert row.cells[1].value == "—"


# ============================================================================
# TransactionRuleV2Formatter — actions
# ============================================================================


def test_rule_set_category_action():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(
        _rule(
            setCategoryAction={
                "id": "cat_1",
                "name": "Food & Drink",
                "icon": "🍔",
                "__typename": "Category",
            }
        )
    )
    assert "Food & Drink" in row.cells[2].value  # Category column


def test_rule_set_merchant_action():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(
        _rule(setMerchantAction={"id": "m1", "name": "Netflix", "__typename": "Merchant"})
    )
    assert "Netflix" in row.cells[3].value  # Merchant column


def test_rule_no_actions_shows_dash():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(_rule())
    assert row.cells[2].value == "—"  # Category
    assert row.cells[3].value == "—"  # Merchant
    assert row.cells[4].value == "—"  # Review


# ============================================================================
# TransactionRuleV2Formatter — expando
# ============================================================================


def test_rule_tags_in_other_column():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(
        _rule(
            addTagsAction=[
                {
                    "id": "t1",
                    "name": "Subscription",
                    "color": "#ffcb12",
                    "__typename": "TransactionTag",
                },
                {
                    "id": "t2",
                    "name": "Personal",
                    "color": "#ff6b6b",
                    "__typename": "TransactionTag",
                },
            ]
        )
    )
    assert row.expando == []
    other = row.cells[5].value
    assert "• tags: Subscription, Personal" in other


def test_rule_review_status_column():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(_rule(reviewStatusAction="reviewed"))
    assert row.cells[4].value == "reviewed"
    assert row.cells[4].color == "green"


def test_rule_no_expando_when_defaults():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(_rule())
    assert row.expando == []


def test_rule_notify_in_other_column():
    fmt = TransactionRuleV2Formatter()
    row = fmt.format(_rule(sendNotificationAction=True))
    assert row.expando == []
    assert "• notify: true" in row.cells[5].value


# ============================================================================
# render_table
# ============================================================================


def _capture_render(table: RenderTable) -> str:
    """Render a table without color and capture output."""
    runner = CliRunner()
    output_lines = []
    with runner.isolated_filesystem():
        import click as _click

        original_echo = _click.echo

        def capture_echo(msg="", **kwargs):
            if not kwargs.get("err"):
                output_lines.append(str(msg))

        _click.echo = capture_echo
        try:
            render_table(table, use_color=False)
        finally:
            _click.echo = original_echo
    return "\n".join(output_lines)


def test_render_table_headers_present():
    table = RenderTable(
        headers=["Col A", "Col B"],
        rows=[TableRow(cells=[Cell("foo"), Cell("bar")])],
    )
    out = _capture_render(table)
    assert "Col A" in out
    assert "Col B" in out
    assert "foo" in out
    assert "bar" in out


def test_render_table_separator_lines():
    table = RenderTable(
        headers=["X"],
        rows=[TableRow(cells=[Cell("val")])],
    )
    out = _capture_render(table)
    lines = out.split("\n")
    separator_lines = [ln for ln in lines if set(ln.strip()) == {"─"}]
    assert len(separator_lines) == 2  # header separator + footer


def test_render_table_expando_prefix():
    table = RenderTable(
        headers=["Col"],
        rows=[
            TableRow(
                cells=[Cell("value")],
                expando=[
                    ExpandoBlock(
                        lines=[
                            ExpandoLine(segments=[StyledSegment("tags: "), StyledSegment("Foo")])
                        ]
                    )
                ],
            )
        ],
    )
    out = _capture_render(table)
    assert "┗━" in out
    assert "tags: " in out
    assert "Foo" in out


def test_render_table_multiline_cell():
    table = RenderTable(
        headers=["A", "B"],
        rows=[TableRow(cells=[Cell("line1\nline2"), Cell("single")])],
    )
    out = _capture_render(table)
    assert "line1" in out
    assert "line2" in out


def test_render_table_truncation():
    table = RenderTable(
        headers=["Col"],
        rows=[TableRow(cells=[Cell("this is a long string", max_width=10)])],
    )
    out = _capture_render(table)
    assert "…" in out
    # Truncated value must appear
    assert "this is a" in out


def test_render_table_no_headers_default_formatter():
    """DefaultFormatter produces no headers — should render expando only, no separator."""
    table = RenderTable(
        headers=[],
        rows=[
            TableRow(
                cells=[],
                expando=[
                    ExpandoBlock(
                        lines=[ExpandoLine(segments=[StyledSegment("key: "), StyledSegment("val")])]
                    )
                ],
            )
        ],
    )
    out = _capture_render(table)
    assert "key: " in out
    assert "val" in out
    # No separator lines since no headers
    assert "─" not in out


# ============================================================================
# output_pretty integration
# ============================================================================


def test_output_pretty_transaction_rules(capsys):
    data = {
        "transactionRules": [
            {
                "__typename": "TransactionRuleV2",
                "id": "rule_001",
                "order": 0,
                "merchantCriteria": None,
                "originalStatementCriteria": [{"operator": "contains", "value": "netflix"}],
                "merchantNameCriteria": None,
                "amountCriteria": None,
                "categories": [],
                "accounts": [],
                "criteriaOwnerIsJoint": False,
                "criteriaOwnerUsers": None,
                "setCategoryAction": {
                    "id": "c1",
                    "name": "Entertainment",
                    "icon": "🎬",
                    "__typename": "Category",
                },
                "setMerchantAction": None,
                "addTagsAction": None,
                "reviewStatusAction": None,
                "sendNotificationAction": False,
                "setHideFromReportsAction": False,
                "needsReviewByUserAction": None,
                "splitTransactionsAction": None,
            }
        ]
    }
    output_pretty(data, use_color=False)
    captured = capsys.readouterr().out
    assert "netflix" in captured
    assert "Entertainment" in captured
    assert "Criteria" in captured


def test_output_pretty_empty_data(capsys):
    output_pretty({}, use_color=False)
    assert capsys.readouterr().out == ""


def test_output_pretty_unknown_typename(capsys):
    data = [{"__typename": "UnknownType", "foo": "bar", "count": 42}]
    output_pretty(data, use_color=False)
    captured = capsys.readouterr().out
    assert "foo" in captured
    assert "bar" in captured
