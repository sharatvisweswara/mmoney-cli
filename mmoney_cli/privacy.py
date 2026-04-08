"""Privacy.com virtual card transaction triage logic.

Privacy.com transactions appear in Monarch Money with plaidName format:
  PwP <merchant_fragment> Privacycom

The merchant fragment is truncated (~14-16 chars) and the spacing after
PwP varies (1 or 2 spaces). This module extracts, groups, and matches
those fragments against existing transaction rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ============================================================================
# plaidName parsing
# ============================================================================

_PWP_PATTERN = re.compile(
    r"^PwP\s+(.+?)\s+Privacycom$",
    re.IGNORECASE,
)

# Minimum prefix length for grouping — prevents false merges like
# "SQ *THE" matching "SQ *MEDITER"
_MIN_PREFIX_LEN = 6


def parse_privacy_plaid_name(plaid_name: str | None) -> str | None:
    """Extract merchant fragment from a Privacy.com plaidName.

    Returns the stripped fragment, or None if not a Privacy.com transaction.
    """
    if not plaid_name:
        return None
    m = _PWP_PATTERN.match(plaid_name)
    return m.group(1).strip() if m else None


# ============================================================================
# Data classes
# ============================================================================


@dataclass
class RuleMatch:
    """An existing rule that matches or partially matches a merchant fragment."""

    rule: dict[str, Any]
    match_type: str  # "covers" | "partial"
    rule_merchant: str  # merchant name the rule sets


@dataclass
class MerchantGroup:
    """A group of transactions from the same Privacy.com merchant."""

    canonical: str  # shortest fragment (lowercase)
    variants: dict[str, int] = field(default_factory=dict)  # fragment -> count
    transactions: list[dict[str, Any]] = field(default_factory=list)
    total_amount: float = 0.0
    matching_rules: list[RuleMatch] = field(default_factory=list)
    suggested_merchant: str | None = None  # from Privacy.com card memo
    merchant_descriptor: str | None = None  # full untruncated name from Privacy.com

    @property
    def status(self) -> str:
        if any(rm.match_type == "covers" for rm in self.matching_rules):
            return "covered"
        if any(rm.match_type == "partial" for rm in self.matching_rules):
            return "partial"
        return "needs_rule"


# ============================================================================
# Grouping
# ============================================================================


def group_by_merchant(transactions: list[dict[str, Any]]) -> list[MerchantGroup]:
    """Group Privacy.com transactions by extracted merchant fragment.

    Fragments that are prefixes of each other (e.g., CLAUDE.AI S and
    CLAUDE.AI SU) are merged into one group. The shortest variant becomes
    the canonical form, since ``contains`` with the shortest prefix matches
    all variants.
    """
    # Extract fragments, skip non-privacy
    parsed: list[tuple[str, dict[str, Any]]] = []
    for txn in transactions:
        frag = parse_privacy_plaid_name(txn.get("plaidName"))
        if frag:
            parsed.append((frag, txn))

    if not parsed:
        return []

    # Build initial per-fragment groups
    frag_groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for frag, txn in parsed:
        upper = frag.upper()
        if upper not in frag_groups:
            frag_groups[upper] = []
        frag_groups[upper].append((frag, txn))

    # Merge groups whose keys are prefixes of each other
    keys = sorted(frag_groups.keys(), key=len)
    merged: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    key_map: dict[str, str] = {}  # maps original key -> merged canonical key

    for key in keys:
        found = False
        for canonical in list(merged.keys()):
            if key.startswith(canonical) and len(canonical) >= _MIN_PREFIX_LEN:
                merged[canonical].extend(frag_groups[key])
                key_map[key] = canonical
                found = True
                break
        if not found:
            merged[key] = list(frag_groups[key])
            key_map[key] = key

    # Also merge longer keys that are prefixes of even longer keys
    # (handles case where we see the shorter key after the longer one)
    final: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for canonical, items in merged.items():
        absorbed = False
        for existing in list(final.keys()):
            shorter, longer = sorted([canonical, existing], key=len)
            if longer.startswith(shorter) and len(shorter) >= _MIN_PREFIX_LEN:
                final[shorter if shorter == existing else existing] = (
                    final.pop(existing) if shorter != existing else final[existing]
                )
                if shorter != existing:
                    final[shorter] = final.pop(existing) + items
                else:
                    final[existing].extend(items)
                absorbed = True
                break
        if not absorbed:
            final[canonical] = items

    # Build MerchantGroup objects
    groups: list[MerchantGroup] = []
    for _canonical_upper, items in final.items():
        variants: dict[str, int] = {}
        txns: list[dict[str, Any]] = []
        total = 0.0
        for frag, txn in items:
            variants[frag] = variants.get(frag, 0) + 1
            txns.append(txn)
            total += txn.get("amount", 0) or 0

        # Canonical = shortest variant, lowercased (for rule matching)
        shortest = min(variants.keys(), key=len)
        groups.append(
            MerchantGroup(
                canonical=shortest.lower(),
                variants=variants,
                transactions=txns,
                total_amount=total,
            )
        )

    # Sort by transaction count descending
    groups.sort(key=lambda g: len(g.transactions), reverse=True)
    return groups


# ============================================================================
# Rule matching
# ============================================================================

_PWP_PREFIX_RE = re.compile(r"^pwp\s+", re.IGNORECASE)


def _strip_pwp_prefix(value: str) -> str:
    """Strip 'pwp ' or 'pwp  ' prefix from a rule value for comparison."""
    return _PWP_PREFIX_RE.sub("", value)


def _is_catch_all_rule(rule: dict[str, Any]) -> bool:
    """Check if a rule is the generic Privacy catch-all."""
    merchant = rule.get("setMerchantAction")
    if isinstance(merchant, dict):
        return merchant.get("name", "").lower() == "privacy"
    return False


def _prefix_overlap(a: str, b: str) -> int:
    """Length of the longest common prefix between two strings."""
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    return i


def find_similar_rules(fragment: str, rules: list[dict[str, Any]]) -> list[RuleMatch]:
    """Find existing rules that match or nearly match a merchant fragment.

    Returns list of RuleMatch with match_type:
      - "covers": rule would match transactions with this fragment
      - "partial": rule likely targets same merchant (prefix overlap)
    """
    frag_lower = fragment.lower()
    matches: list[RuleMatch] = []

    for rule in rules:
        if _is_catch_all_rule(rule):
            continue

        merchant_action = rule.get("setMerchantAction")
        rule_merchant = ""
        if isinstance(merchant_action, dict):
            rule_merchant = merchant_action.get("name", "")
        elif isinstance(merchant_action, str):
            rule_merchant = merchant_action

        for criterion in rule.get("originalStatementCriteria") or []:
            rule_val = criterion["value"].lower()
            rule_op = criterion["operator"]

            # Strip pwp prefix for comparison against our fragment
            rule_frag = _strip_pwp_prefix(rule_val)

            if rule_op == "contains":
                # Does this rule's fragment cover our transactions?
                if rule_frag in frag_lower or frag_lower.startswith(rule_frag):
                    matches.append(RuleMatch(rule, "covers", rule_merchant))
                elif _prefix_overlap(rule_frag, frag_lower) >= _MIN_PREFIX_LEN:
                    matches.append(RuleMatch(rule, "partial", rule_merchant))
            elif rule_op == "eq":
                # Strip pwp prefix and privacycom suffix from eq rule value
                eq_frag = rule_frag
                if eq_frag.endswith("privacycom"):
                    eq_frag = eq_frag[: -len("privacycom")].strip()
                if eq_frag == frag_lower:
                    matches.append(RuleMatch(rule, "covers", rule_merchant))
                elif _prefix_overlap(eq_frag, frag_lower) >= _MIN_PREFIX_LEN:
                    matches.append(RuleMatch(rule, "partial", rule_merchant))

    return matches


# ============================================================================
# Command suggestion
# ============================================================================


def build_rule_command(
    fragment: str,
    merchant: str | None = None,
    category: str | None = None,
) -> str:
    """Build a suggested mmoney privacy rule command."""
    parts = ["mmoney --allow-mutations privacy rule"]
    parts.append(f'-s "{fragment}"')
    if merchant:
        parts.append(f'-m "{merchant}"')
    else:
        parts.append('-m "<NAME>"')
    if category:
        parts.append(f'-c "{category}"')
    return " ".join(parts)


# ============================================================================
# Scan orchestrator
# ============================================================================


# ============================================================================
# Privacy.com API enrichment
# ============================================================================


def match_amount_in_window(
    amount_cents: int,
    privacy_txns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find Privacy transactions matching an amount in cents."""
    return [
        pt
        for pt in privacy_txns
        if abs(pt.get("settled_amount") or pt.get("amount") or 0) == amount_cents
    ]


_MERCHANT_LOCKED_TYPES = {"MERCHANT_LOCKED"}


def _clean_descriptor(descriptor: str) -> str:
    """Turn a raw merchant descriptor into a presentable merchant name.

    Strips common prefixes (SQ *, TST*, FS *, etc.), trailing punctuation,
    and title-cases the result.
    """
    s = descriptor.strip().rstrip("-,.")
    # Strip common payment-processor prefixes
    for prefix in (
        "SQ *",
        "TST*",
        "TST* ",
        "FS *",
        "FSP*",
        "SP ",
        "IN *",
        "RF *",
        "OTT* ",
        "PAYPAL *",
    ):
        if s.upper().startswith(prefix):
            s = s[len(prefix) :]
            break
    return s.strip().title()


def enrich_groups_from_privacy(
    groups: list[MerchantGroup],
    search_privacy: Any,
    get_card_info: Any,
) -> list[MerchantGroup]:
    """Enrich merchant groups with data from Privacy.com.

    For each group, picks a representative transaction, searches Privacy
    by amount within a date window, and enriches with card memo + descriptor.

    :param groups: MerchantGroup list to enrich in-place.
    :param search_privacy: Callable(amount_cents, date_str) -> list[dict],
        returns Privacy transactions matching amount near the date.
    :param get_card_info: Callable(card_token) -> dict|None, returns card
        object with at least 'memo' and 'type' fields.
    :returns: List of unmatched groups (those needing rules but with no Privacy match).
    """
    card_cache: dict[str, dict[str, Any] | None] = {}
    unmatched: list[MerchantGroup] = []

    for group in groups:
        # Pick the most recent transaction as representative
        rep = max(group.transactions, key=lambda t: t.get("date", ""))
        amount_cents = abs(round((rep.get("amount") or 0) * 100))
        rep_date = (rep.get("date") or "")[:10]

        if not amount_cents or not rep_date:
            if group.status != "covered":
                unmatched.append(group)
            continue

        matches = search_privacy(amount_cents, rep_date)
        if not matches:
            if group.status != "covered":
                unmatched.append(group)
            continue

        matched = matches[0]

        # Get merchant descriptor (full untruncated name)
        merchant = matched.get("merchant") or {}
        descriptor = merchant.get("descriptor") or ""
        if descriptor:
            group.merchant_descriptor = descriptor.strip()

        # Get card info
        card_token = matched.get("card_token")
        if card_token and card_token not in card_cache:
            card_cache[card_token] = get_card_info(card_token)

        card = card_cache.get(card_token) if card_token else None
        card_type = (card.get("type") or "") if card else ""
        memo = (card.get("memo") or "") if card else ""

        # Use card memo for merchant-locked cards, cleaned descriptor otherwise
        if card_type in _MERCHANT_LOCKED_TYPES and memo:
            group.suggested_merchant = memo
        elif descriptor:
            group.suggested_merchant = _clean_descriptor(descriptor)

    return unmatched


def scan_privacy_transactions(
    transactions: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    groups: list[MerchantGroup] | None = None,
) -> list[dict[str, Any]]:
    """Scan transactions and return grouped results with rule matching.

    If groups is provided (e.g., pre-enriched with Privacy.com data),
    uses those instead of re-grouping from transactions.

    Returns a list of dicts suitable for output_result(), each with
    __typename "PrivacyScanGroup" for pretty formatting.
    """
    if groups is None:
        groups = group_by_merchant(transactions)

    results: list[dict[str, Any]] = []
    for group in groups:
        group.matching_rules = find_similar_rules(group.canonical, rules)

        suggested_cmd = None
        if group.status != "covered":
            suggested_cmd = build_rule_command(
                group.canonical,
                merchant=group.suggested_merchant,
            )

        results.append(
            {
                "__typename": "PrivacyScanGroup",
                "canonical": group.canonical,
                "variants": group.variants,
                "transaction_count": len(group.transactions),
                "total_amount": group.total_amount,
                "status": group.status,
                "matching_rules": [
                    {
                        "order": rm.rule.get("order"),
                        "match_type": rm.match_type,
                        "merchant": rm.rule_merchant,
                    }
                    for rm in group.matching_rules
                ],
                "suggested_merchant": group.suggested_merchant,
                "merchant_descriptor": group.merchant_descriptor,
                "suggested_command": suggested_cmd,
            }
        )

    return results
