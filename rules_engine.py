"""
rules_engine.py

Evaluates the YAML rule files in rules/ against a canonical dict
(produced by engine/canonical_mapper.py). Deliberately NOT eval()-based:
conditions are a small closed DSL (all/any/not + field/op/value), so rules
can be safely authored, reviewed, and diffed by non-engineers (e.g.
compliance) without code-review risk.

Public entry points:
    evaluate_domain(rules_doc, canonical) -> (outcome, rule_id)
    apply_supplementary(rules_doc, canonical, domain_outcomes) -> (adjusted_outcomes, triggered_flags)
    combine_decision(decision_doc, domain_outcomes) -> (overall, matched_row)
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any


# --------------------------------------------------------------------------
# Field access
# --------------------------------------------------------------------------

def get_field(data: dict, path: str) -> Any:
    """Dotted-path lookup, e.g. 'identity.identity_check_score'. Missing -> None."""
    node = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# --------------------------------------------------------------------------
# Condition DSL evaluator
# --------------------------------------------------------------------------

_OPS = {
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "between": lambda a, b: a is not None and b[0] <= a <= b[1],
    "contains": lambda a, b: isinstance(a, (list, str)) and b in a,
    "not_empty": lambda a, b: bool(a) if b else not bool(a),
    "exists": lambda a, b: (a is not None) if b else (a is None),
}


def evaluate_condition(condition: dict, canonical: dict, computed_registry: dict) -> bool:
    """Recursively evaluate one condition node."""
    if "all" in condition:
        return all(evaluate_condition(c, canonical, computed_registry) for c in condition["all"])
    if "any" in condition:
        return any(evaluate_condition(c, canonical, computed_registry) for c in condition["any"])
    if "not" in condition:
        return not evaluate_condition(condition["not"], canonical, computed_registry)
    if condition.get("type") == "computed":
        fn_name = condition["function"]
        fn = computed_registry.get(fn_name)
        if fn is None:
            raise KeyError(f"No computed function registered for '{fn_name}'")
        return bool(fn(canonical, **condition.get("args", {})))

    # Leaf: {field, op, value}
    field_value = get_field(canonical, condition["field"])
    op = _OPS[condition["op"]]
    return bool(op(field_value, condition["value"]))


# --------------------------------------------------------------------------
# Domain evaluation (identity.yaml / owner.yaml / account.yaml)
# --------------------------------------------------------------------------

def evaluate_domain(rules_doc: dict, canonical: dict, computed_registry: dict | None = None) -> tuple[str, str]:
    """
    rules_doc: parsed YAML for one domain (identity/owner/account).
    Returns (outcome, rule_id). Order is fixed by rules_doc['evaluation_order'],
    first matching rule within a bucket wins; buckets themselves are checked
    in the given order (fail before verified before review).
    """
    computed_registry = computed_registry or {}
    rules = rules_doc["rules"]
    for bucket in rules_doc["evaluation_order"]:
        for rule in rules.get(bucket, []):
            if evaluate_condition(rule["condition"], canonical, computed_registry):
                return bucket, rule["id"]
    return rules_doc["default_outcome"], rules_doc["default_rule_id"]


# --------------------------------------------------------------------------
# Supplementary rules (A-I): flags + outcome caps/overrides
# --------------------------------------------------------------------------

def apply_supplementary(
    rules_doc: dict,
    canonical: dict,
    domain_outcomes: dict[str, str],
    computed_registry: dict | None = None,
) -> tuple[dict[str, str], list[dict]]:
    """
    Returns (adjusted_domain_outcomes, triggered_rules) where triggered_rules
    is a list of {id, category, effect} for every rule whose condition matched
    (whether or not it changed an outcome) -- this list is what feeds the LLM
    explainability step's reason codes.
    """
    computed_registry = computed_registry or {}
    rank = rules_doc["outcome_rank"]
    outcomes = dict(domain_outcomes)
    triggered = []

    for rule in rules_doc["rules"]:
        if not evaluate_condition(rule["condition"], canonical, computed_registry):
            continue

        effect = rule["effect"]
        triggered.append({"id": rule["id"], "category": rule["category"], "effect": effect["type"]})

        if effect["type"] == "cap_outcome":
            domain = effect["domain"]
            max_outcome = effect["max_outcome"]
            if rank[outcomes[domain]] > rank[max_outcome]:
                outcomes[domain] = max_outcome

        elif effect["type"] == "force_outcome":
            domain = effect["domain"]
            outcomes[domain] = effect["forced_outcome"]

        # flag_only / exclude_from_decisioning: no outcome change, already recorded above

    return outcomes, triggered


# --------------------------------------------------------------------------
# Decision table
# --------------------------------------------------------------------------

def _row_matches(row: dict, domain_outcomes: dict[str, str]) -> bool:
    for domain in ("identity", "owner", "account"):
        expected = row[domain]
        actual = domain_outcomes[domain]
        if expected == "any":
            continue
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def combine_decision(decision_doc: dict, domain_outcomes: dict[str, str]) -> tuple[str, dict | None]:
    for row in decision_doc["rows"]:
        if _row_matches(row, domain_outcomes):
            return row["overall"], row
    return decision_doc["default_overall"], None


# --------------------------------------------------------------------------
# Computed function registry for supplementary.yaml
# --------------------------------------------------------------------------

def cross_vendor_name_disagreement(canonical: dict) -> bool:
    """A1: GIACT says name matches but neither Ekata name-match path agrees, or vice versa."""
    giact_name = canonical["identity"]["name_match"]
    ekata_name = canonical["identity"]["phone_name_match"] or canonical["identity"]["email_name_match"] \
        or canonical["owner"]["ekata_address_name_match"]
    # Only a real disagreement if one vendor has an opinion and it conflicts.
    return giact_name != ekata_name


def ssn_geography_mismatch(canonical: dict) -> bool:
    """B1: SSN issue state isn't among any address state on file."""
    ssn_state = canonical["supplementary"]["ssn_issue_state"]
    known_states = canonical["supplementary"]["known_address_states"]
    if not ssn_state or not known_states:
        return False
    return ssn_state not in known_states


def ssn_issuance_age_out_of_range(canonical: dict, min_age: int = 14, max_age: int = 21) -> bool:
    """B2: SSN issue year minus birth year should typically fall in [min_age, max_age]."""
    dob = canonical["supplementary"]["date_of_birth"]
    issue_year = canonical["supplementary"]["ssn_issue_start_year"]
    if not dob or not issue_year:
        return False
    birth_year = int(dob[:4])
    age_at_issue = issue_year - birth_year
    return not (min_age <= age_at_issue <= max_age)


def current_address_reported_within_days(canonical: dict, days: int = 30) -> bool:
    """C2: flags a freshly-reported 'Current' address as higher risk. Requires the
    raw DateReported for the matched current record -- passed through supplementary
    if the mapper chooses to surface it; defaults to False if not present."""
    reported_days_ago = canonical["supplementary"].get("current_address_reported_days_ago")
    return reported_days_ago is not None and reported_days_ago < days


def new_email_new_account(canonical: dict, days: int = 30) -> bool:
    """E2: both the email and the account are recently created."""
    email_days = canonical["supplementary"]["email_first_seen_days"]
    account_added = canonical["account"]["account_added_date"]
    if email_days is None or not account_added:
        return False
    try:
        added_dt = datetime.fromisoformat(account_added.replace("Z", "+00:00"))
        if added_dt.tzinfo is None:
            added_dt = added_dt.replace(tzinfo=timezone.utc)
        account_age_days = (datetime.now(timezone.utc) - added_dt).days
    except ValueError:
        return False
    return email_days < days and account_age_days < days


def dormant_reactivated(canonical: dict, dormant_years: int = 2, reactivated_days: int = 3) -> bool:
    """H1: account opened long ago, but updated very recently."""
    added = canonical["account"]["account_added_date"]
    updated = canonical["account"]["account_last_updated_date"]
    if not added or not updated:
        return False
    try:
        added_dt = datetime.fromisoformat(added.replace("Z", "+00:00"))
        updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    if added_dt.tzinfo is None:
        added_dt = added_dt.replace(tzinfo=timezone.utc)
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=timezone.utc)
    account_age_years = (now - added_dt).days / 365.25
    days_since_update = (now - updated_dt).days
    return account_age_years > dormant_years and days_since_update <= reactivated_days


def account_type_mismatch(canonical: dict) -> bool:
    """H2: placeholder -- requires the bank's own record type from a richer GIACT
    response tier; returns False until that field is available in the payload."""
    return False


def score_band_overlay(canonical: dict) -> bool:
    """I1: always 'triggers' as an informational overlay; the band itself is
    computed for the explainability payload, not used to gate outcomes here."""
    return True


def ssn_issuance_year_implausible(canonical: dict) -> bool:
    """J1: reasonableness check on GIACT-RETURNED data (not submitted data) --
    SsnIssueStartYear should fall between 1936 (when the SSN program began)
    and the current year. A value outside that range means the vendor's own
    data is internally implausible -- this does NOT correct it, only flags
    it (see rules/supplementary.yaml's effect: flag_only), per the
    2026-08-08 working call: "we are not proposing the fix we are flagging
    it.\""""
    issue_year = canonical["supplementary"]["ssn_issue_start_year"]
    if not issue_year:
        return False
    current_year = datetime.now(timezone.utc).year
    return not (1936 <= issue_year <= current_year)


def vendor_returned_date_implausible(canonical: dict) -> bool:
    """J2: reasonableness check on GIACT-RETURNED account dates -- none of
    account_added_date / account_last_updated_date / account_closed_date
    should be in the future. This is the literal example raised on the
    2026-08-08 call ("if the date of birth is 1/1/2030, are we passing
    that along") -- our canonical schema doesn't carry a vendor-returned
    date of birth field (vendors only echo back a match/no-match on the
    submitted DOB, never their own copy of it), so this applies the same
    principle to the account dates GIACT does actually return."""
    for field in ("account_added_date", "account_last_updated_date", "account_closed_date"):
        raw = canonical["account"].get(field)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt > datetime.now(timezone.utc):
            return True
    return False


COMPUTED_REGISTRY = {
    "cross_vendor_name_disagreement": cross_vendor_name_disagreement,
    "ssn_geography_mismatch": ssn_geography_mismatch,
    "ssn_issuance_age_out_of_range": ssn_issuance_age_out_of_range,
    "current_address_reported_within_days": current_address_reported_within_days,
    "new_email_new_account": new_email_new_account,
    "dormant_reactivated": dormant_reactivated,
    "account_type_mismatch": account_type_mismatch,
    "score_band_overlay": score_band_overlay,
    "ssn_issuance_year_implausible": ssn_issuance_year_implausible,
    "vendor_returned_date_implausible": vendor_returned_date_implausible,
}
