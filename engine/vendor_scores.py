"""
vendor_scores.py

Per-vendor "as received" score/code for the waterfall demo -- shown at the
TOP of the results view (see ui/app.py), unmodified from what the vendor
actually returned. This is the "never blended" layer: whichever vendor
answered, its own native score or code is shown attributed to that vendor
alone, never folded into another vendor's number.

Grounded in real vendor documentation, not invented fields:
  - EWS (AVS): native 0-100 `overallMatchScore`, from the client's actual
    sample AVS request/response.
  - GIACT (LSEG): real GIACT Verification Services API (SOAP, v5.86) --
    NO numeric score exists in real GIACT responses. GIACT is a CODE
    vendor: VerificationResponse (Pass/AcceptWithRisk/RiskAlert/
    NegativeData), AccountResponseCode, CustomerResponseCode.
  - Ekata: native 0-500 identity_check_score (unchanged from before -- no
    real Ekata doc was reviewed this session).

Each function returns:
    {"vendor": <display name>, "status": "success" | "no_data" | "not_called",
     "native_score": <raw value from the vendor, number or code string, or None>,
     "native_scale": <human description of the vendor's own scale>,
     "score": int 0-100 | None,          -- kept for backward compatibility
                                             with engine/vendor_decision.py's
                                             Go/No-Go/High Risk/Hard Decline bands
     "normalized_score": float 0-10 | None,   -- engine/vendor_normalization.py
     "checks": [{"label": str, "value": str}, ...]}

"checks" is the per-signal breakdown shown alongside the score/code, as
returned by the vendor -- not used to compute the score itself anymore
(the real native score/code IS the score now), just shown for transparency.
"""

from __future__ import annotations
from typing import Any, Callable, Optional

from engine.vendor_normalization import normalize_ews_score, normalize_giact_response, normalize_ekata_score


def ews_score(raw: Optional[dict]) -> dict[str, Any]:
    if not raw:
        return {"vendor": "EWS", "status": "not_called", "native_score": None, "native_scale": "0-100",
                "score": None, "normalized_score": None, "checks": []}

    success = raw.get("SuccessResponse") or {}
    owner_match = success.get("accountOwnerMatch")
    account_status = (success.get("accountStatus") or {}).get("status")

    if owner_match is None:
        return {"vendor": "EWS", "status": "no_data", "native_score": None, "native_scale": "0-100",
                "score": None, "normalized_score": None,
                "checks": [{"label": "Account status", "value": account_status or "Unknown"}]}

    overall = owner_match.get("overallMatchScore")
    field_labels = {
        "firstNameMatch": "First name match", "lastNameMatch": "Last name match",
        "businessNameMatch": "Business name match", "addressMatch": "Address match",
        "cityMatch": "City match", "stateMatch": "State match", "zipMatch": "Zip match",
        "homePhoneMatch": "Home phone match",
    }
    checks = [
        {"label": label, "value": owner_match[key]}
        for key, label in field_labels.items()
        if owner_match.get(key) is not None
    ]
    checks.append({"label": "Condition code", "value": f"{owner_match.get('conditionCode')} ({owner_match.get('conditionCodeMessage')})"})

    return {
        "vendor": "EWS", "status": "success",
        "native_score": overall, "native_scale": "0-100 (overallMatchScore)",
        "score": overall,  # already native 0-100 -- no conversion needed
        "normalized_score": normalize_ews_score(overall),
        "checks": checks,
    }


def lseg_score(raw: Optional[dict]) -> dict[str, Any]:
    """LSEG = the existing giact_verify tool, relabeled for the demo (see
    config/waterfall_rules.yaml). Reads the REAL GIACT PostInquiryResult
    fields -- GIACT has no native numeric score, so "score" here is
    derived entirely from the real VerificationResponse code band."""
    if not raw:
        return {"vendor": "LSEG", "status": "not_called", "native_score": None,
                "native_scale": "Pass / AcceptWithRisk / RiskAlert / NegativeData",
                "score": None, "normalized_score": None, "checks": []}
    matched = raw.get("MatchedPersonData") or []
    verification_response = raw.get("VerificationResponse")
    if not matched and verification_response is None:
        return {"vendor": "LSEG", "status": "no_data", "native_score": None,
                "native_scale": "Pass / AcceptWithRisk / RiskAlert / NegativeData",
                "score": None, "normalized_score": None, "checks": []}

    normalized = normalize_giact_response(verification_response)
    ofac_clear = not raw.get("OfacListPotentialMatches")
    checks = [
        {"label": "Verification response", "value": verification_response or "Unknown"},
        {"label": "Account response code", "value": raw.get("AccountResponseCode") or "\u2014"},
        {"label": "Customer response code", "value": raw.get("CustomerResponseCode") or "\u2014"},
        {"label": "OFAC screening clear", "value": "Yes" if ofac_clear else "No"},
    ]
    return {
        "vendor": "LSEG", "status": "success",
        "native_score": verification_response, "native_scale": "Pass / AcceptWithRisk / RiskAlert / NegativeData",
        "score": round(normalized * 10) if normalized is not None else None,
        "normalized_score": normalized,
        "checks": checks,
    }


def ekata_score(raw: Optional[dict]) -> dict[str, Any]:
    if not raw:
        return {"vendor": "Ekata", "status": "not_called", "native_score": None, "native_scale": "0-500",
                "score": None, "normalized_score": None, "checks": []}
    ident = raw.get("identity_check_score")
    if ident is None:
        return {"vendor": "Ekata", "status": "no_data", "native_score": None, "native_scale": "0-500",
                "score": None, "normalized_score": None, "checks": []}
    checks = [{"label": "Identity check score", "value": f"{ident}/500"}]
    return {
        "vendor": "Ekata", "status": "success",
        "native_score": ident, "native_scale": "0-500 (identity_check_score)",
        "score": round(ident / 500 * 100),
        "normalized_score": normalize_ekata_score(ident),
        "checks": checks,
    }


VENDOR_SCORERS: dict[str, Callable[[Optional[dict]], dict[str, Any]]] = {
    "ews_check": ews_score,
    "giact_verify": lseg_score,
    "ekata_identity_check": ekata_score,
}
