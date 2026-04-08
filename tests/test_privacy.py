"""Tests for mmoney_cli/privacy.py."""

from mmoney_cli.privacy import (
    MerchantGroup,
    RuleMatch,
    build_rule_command,
    enrich_groups_from_privacy,
    find_similar_rules,
    group_by_merchant,
    match_amount_in_window,
    parse_privacy_plaid_name,
    scan_privacy_transactions,
)

# ============================================================================
# parse_privacy_plaid_name
# ============================================================================


def test_parse_double_space():
    assert parse_privacy_plaid_name("PwP  CLAUDE.AI S Privacycom") == "CLAUDE.AI S"


def test_parse_single_space():
    assert parse_privacy_plaid_name("PwP CLAUDE.AI SU Privacycom") == "CLAUDE.AI SU"


def test_parse_short_merchant():
    assert parse_privacy_plaid_name("PwP  ORCA Privacycom") == "ORCA"


def test_parse_lowercase_variation():
    assert parse_privacy_plaid_name("pwp  claude.ai s privacycom") == "claude.ai s"


def test_parse_non_privacy():
    assert parse_privacy_plaid_name("AMAZON MARKETPLACE") is None


def test_parse_none():
    assert parse_privacy_plaid_name(None) is None


def test_parse_empty():
    assert parse_privacy_plaid_name("") is None


def test_parse_strips_whitespace():
    # Extra spaces between fragment and Privacycom
    assert parse_privacy_plaid_name("PwP  SPROUTS FAR  Privacycom") == "SPROUTS FAR"


# ============================================================================
# group_by_merchant
# ============================================================================


def _txn(plaid_name, amount=-10.0):
    return {"plaidName": plaid_name, "amount": amount, "id": plaid_name}


def test_group_distinct_merchants():
    txns = [
        _txn("PwP  SPROUTS FAR Privacycom"),
        _txn("PwP  GOOGLE*YOUT Privacycom"),
    ]
    groups = group_by_merchant(txns)
    assert len(groups) == 2
    canonicals = {g.canonical for g in groups}
    assert "sprouts far" in canonicals
    assert "google*yout" in canonicals


def test_group_merges_truncated_variants():
    txns = [
        _txn("PwP CLAUDE.AI SU Privacycom"),
        _txn("PwP  CLAUDE.AI S Privacycom"),
        _txn("PwP  CLAUDE.AI S Privacycom"),
    ]
    groups = group_by_merchant(txns)
    assert len(groups) == 1
    assert groups[0].canonical == "claude.ai s"  # shortest variant
    assert len(groups[0].transactions) == 3
    assert len(groups[0].variants) == 2


def test_group_does_not_merge_short_prefix():
    """Fragments shorter than _MIN_PREFIX_LEN should not be merged."""
    txns = [
        _txn("PwP  SQ *THE BRI Privacycom"),
        _txn("PwP  SQ *MEDITER Privacycom"),
    ]
    groups = group_by_merchant(txns)
    assert len(groups) == 2


def test_group_single_transaction():
    txns = [_txn("PwP  MIGADU.COM Privacycom")]
    groups = group_by_merchant(txns)
    assert len(groups) == 1
    assert groups[0].transaction_count == 1 if hasattr(groups[0], "transaction_count") else True
    assert len(groups[0].transactions) == 1


def test_group_skips_non_privacy():
    txns = [
        _txn("PwP  SPROUTS FAR Privacycom"),
        {"plaidName": "AMAZON MARKETPLACE", "amount": -50.0, "id": "amz"},
    ]
    groups = group_by_merchant(txns)
    assert len(groups) == 1
    assert groups[0].canonical == "sprouts far"


def test_group_sorted_by_count_descending():
    txns = [
        _txn("PwP  GOOGLE*YOUT Privacycom"),
        _txn("PwP  SPROUTS FAR Privacycom"),
        _txn("PwP  SPROUTS FAR Privacycom"),
        _txn("PwP  SPROUTS FAR Privacycom"),
    ]
    groups = group_by_merchant(txns)
    assert groups[0].canonical == "sprouts far"
    assert groups[1].canonical == "google*yout"


def test_group_total_amount():
    txns = [
        _txn("PwP  SPROUTS FAR Privacycom", amount=-42.30),
        _txn("PwP  SPROUTS FAR Privacycom", amount=-38.15),
    ]
    groups = group_by_merchant(txns)
    assert abs(groups[0].total_amount - (-80.45)) < 0.01


def test_group_empty_input():
    assert group_by_merchant([]) == []


# ============================================================================
# find_similar_rules
# ============================================================================


def _rule(order, operator, value, merchant_name, merchant_id="m1"):
    return {
        "order": order,
        "originalStatementCriteria": [{"operator": operator, "value": value}],
        "setMerchantAction": {"id": merchant_id, "name": merchant_name},
    }


def _catch_all_rule():
    return {
        "order": 6,
        "originalStatementCriteria": [{"operator": "contains", "value": "pwp*privacy.com "}],
        "setMerchantAction": {"id": "m0", "name": "Privacy"},
    }


def test_find_covers_contains_rule():
    """A contains rule whose fragment matches should report 'covers'."""
    rules = [_rule(9, "contains", "pwp  anthropic", "Anthropic")]
    matches = find_similar_rules("anthropic", rules)
    assert len(matches) == 1
    assert matches[0].match_type == "covers"
    assert matches[0].rule_merchant == "Anthropic"


def test_find_covers_contains_without_pwp():
    """A contains rule with just the fragment (no pwp prefix) should match."""
    rules = [_rule(9, "contains", "anthropic", "Anthropic")]
    matches = find_similar_rules("anthropic su", rules)
    assert len(matches) == 1
    assert matches[0].match_type == "covers"


def test_find_covers_eq_rule():
    """An eq rule whose stripped value matches the fragment."""
    rules = [_rule(10, "eq", "pwp  amazon reta privacycom", "Amazon")]
    matches = find_similar_rules("amazon reta", rules)
    assert len(matches) == 1
    assert matches[0].match_type == "covers"


def test_find_partial_match():
    """A rule with a similar prefix should report 'partial'."""
    rules = [_rule(9, "contains", "pwp  google*clou", "Google Cloud")]
    matches = find_similar_rules("google*yout", rules)
    assert len(matches) == 1
    assert matches[0].match_type == "partial"


def test_find_no_match():
    rules = [_rule(9, "contains", "pwp  anthropic", "Anthropic")]
    matches = find_similar_rules("sprouts far", rules)
    assert len(matches) == 0


def test_find_skips_catch_all():
    rules = [_catch_all_rule()]
    matches = find_similar_rules("anything", rules)
    assert len(matches) == 0


def test_find_skips_null_criteria():
    rule = {
        "order": 7,
        "originalStatementCriteria": None,
        "setMerchantAction": {"name": "Something"},
    }
    matches = find_similar_rules("something", [rule])
    assert len(matches) == 0


# ============================================================================
# build_rule_command
# ============================================================================


def test_build_command_basic():
    cmd = build_rule_command("sprouts far")
    assert '-s "sprouts far"' in cmd
    assert '-m "<NAME>"' in cmd
    assert "privacy rule" in cmd


def test_build_command_with_merchant():
    cmd = build_rule_command("sprouts far", merchant="Sprouts")
    assert '-m "Sprouts"' in cmd
    assert "<NAME>" not in cmd


def test_build_command_with_category():
    cmd = build_rule_command("sprouts far", merchant="Sprouts", category="Groceries")
    assert '-c "Groceries"' in cmd


# ============================================================================
# scan_privacy_transactions (integration)
# ============================================================================


def test_scan_groups_and_matches():
    txns = [
        {"plaidName": "PwP  SPROUTS FAR Privacycom", "amount": -42.30},
        {"plaidName": "PwP  SPROUTS FAR Privacycom", "amount": -38.15},
        {"plaidName": "PwP  ANTHROPIC S Privacycom", "amount": -20.00},
    ]
    rules = [_rule(9, "contains", "pwp  anthropic", "Anthropic")]
    results = scan_privacy_transactions(txns, rules)

    assert len(results) == 2
    # Sprouts group (2 txns, should be first)
    sprouts = next(r for r in results if "sprouts" in r["canonical"])
    assert sprouts["transaction_count"] == 2
    assert sprouts["status"] == "needs_rule"
    assert sprouts["suggested_command"] is not None

    # Anthropic group (1 txn, covered)
    anthropic = next(r for r in results if "anthropic" in r["canonical"])
    assert anthropic["transaction_count"] == 1
    assert anthropic["status"] == "covered"
    assert anthropic["suggested_command"] is None


def test_scan_empty():
    results = scan_privacy_transactions([], [])
    assert results == []


def test_scan_typename():
    txns = [{"plaidName": "PwP  TEST Privacycom", "amount": -10.0}]
    results = scan_privacy_transactions(txns, [])
    assert results[0]["__typename"] == "PrivacyScanGroup"


# ============================================================================
# MerchantGroup.status property
# ============================================================================


def test_status_needs_rule():
    g = MerchantGroup(canonical="test")
    assert g.status == "needs_rule"


def test_status_covered():
    g = MerchantGroup(
        canonical="test",
        matching_rules=[RuleMatch(rule={}, match_type="covers", rule_merchant="X")],
    )
    assert g.status == "covered"


def test_status_partial():
    g = MerchantGroup(
        canonical="test",
        matching_rules=[RuleMatch(rule={}, match_type="partial", rule_merchant="X")],
    )
    assert g.status == "partial"


def test_status_covers_trumps_partial():
    g = MerchantGroup(
        canonical="test",
        matching_rules=[
            RuleMatch(rule={}, match_type="partial", rule_merchant="X"),
            RuleMatch(rule={}, match_type="covers", rule_merchant="Y"),
        ],
    )
    assert g.status == "covered"


# ============================================================================
# Privacy.com enrichment
# ============================================================================


def test_match_amount_in_window():
    privacy_txns = [
        {"settled_amount": 18648, "amount": 18648, "card_token": "c1"},
        {"settled_amount": 4230, "amount": 4230, "card_token": "c2"},
    ]
    matches = match_amount_in_window(18648, privacy_txns)
    assert len(matches) == 1
    assert matches[0]["card_token"] == "c1"


def test_match_amount_in_window_no_match():
    privacy_txns = [
        {"settled_amount": 9999, "amount": 9999},
    ]
    assert match_amount_in_window(18648, privacy_txns) == []


def test_enrich_groups_sets_merchant_and_descriptor():
    groups = [
        MerchantGroup(
            canonical="sprouts far",
            transactions=[{"amount": -42.30, "date": "2026-04-01"}],
        ),
    ]

    def mock_search(amount_cents, date_str):
        if amount_cents == 4230:
            return [
                {
                    "card_token": "card-sprouts",
                    "merchant": {"descriptor": "SPROUTS FARMERS MARKET"},
                }
            ]
        return []

    def mock_get_card(token):
        if token == "card-sprouts":
            return {"memo": "Sprouts", "type": "MERCHANT_LOCKED"}
        return None

    unmatched = enrich_groups_from_privacy(groups, mock_search, mock_get_card)
    assert groups[0].suggested_merchant == "Sprouts"
    assert groups[0].merchant_descriptor == "SPROUTS FARMERS MARKET"
    assert unmatched == []


def test_enrich_uses_descriptor_for_unlocked_cards():
    """For UNLOCKED/DIGITAL_WALLET cards, use cleaned descriptor not memo."""
    groups = [
        MerchantGroup(
            canonical="chris nguye",
            transactions=[{"amount": -36.0, "date": "2026-04-01"}],
        ),
    ]

    def mock_search(amount_cents, date_str):
        if amount_cents == 3600:
            return [
                {
                    "card_token": "card-groceries",
                    "merchant": {"descriptor": "CHRIS NGUYEN BARBER"},
                }
            ]
        return []

    def mock_get_card(token):
        return {"memo": "Groceries", "type": "UNLOCKED"}

    unmatched = enrich_groups_from_privacy(groups, mock_search, mock_get_card)
    assert groups[0].suggested_merchant == "Chris Nguyen Barber"
    assert unmatched == []


def test_enrich_groups_returns_unmatched():
    groups = [
        MerchantGroup(
            canonical="unknown merchant",
            transactions=[{"amount": -99.99, "date": "2026-04-01"}],
        ),
    ]
    unmatched = enrich_groups_from_privacy(groups, lambda a, d: [], lambda t: None)
    assert len(unmatched) == 1
    assert unmatched[0].canonical == "unknown merchant"


def test_enrich_groups_skips_covered_from_unmatched():
    """Covered groups without a Privacy match are not reported as unmatched."""
    groups = [
        MerchantGroup(
            canonical="anthropic",
            transactions=[{"amount": -20.0, "date": "2026-04-01"}],
            matching_rules=[RuleMatch(rule={}, match_type="covers", rule_merchant="Anthropic")],
        ),
    ]
    unmatched = enrich_groups_from_privacy(groups, lambda a, d: [], lambda t: None)
    assert unmatched == []


def test_scan_uses_enriched_merchant_in_command():
    txns = [{"plaidName": "PwP  SPROUTS FAR Privacycom", "amount": -42.30, "date": "2026-04-01"}]
    groups = group_by_merchant(txns)
    groups[0].suggested_merchant = "Sprouts"
    results = scan_privacy_transactions(txns, [], groups=groups)
    assert '-m "Sprouts"' in results[0]["suggested_command"]
