"""
vendor_normalization.py

Normalizes each vendor's own native score or response code onto a shared
0-10 scale, per the team decision on the working call (2026-08-06):
"we'll just have it normalized between 0 to 10 based on the reason code
and then add it to our deterministic score."

Grounded in the REAL vendor documentation provided, not invented ranges:
  - EWS (AVS): a real sample response shows `overallMatchScore` on a
    native 0-100 scale -- EWS/AVS is a SCORE vendor.
  - GIACT: the real GIACT Verification Services API (SOAP, v5.86) sample
    doc shows NO numeric score field anywhere in its PostInquiry
    responses -- only categorical codes (VerificationResponse:
    Pass/AcceptWithRisk/RiskAlert/NegativeData; AccountResponseCode;
    CustomerResponseCode). GIACT is a CODE vendor, not a score vendor --
    this matches exactly what was discussed on the call ("some vendors
    provide scores, some provide codes").
  - Ekata: identity_check_score is documented elsewhere in this codebase
    on a native 0-500 scale.

Because GIACT is code-only, its 0-10 band below is a judgment call (Pass
clearly best, NegativeData clearly worst) rather than a documented
numeric mapping -- flagged here rather than presented as if GIACT
published these exact numbers.
"""

from __future__ import annotations
from typing import Optional

EWS_NATIVE_MAX = 100
EKATA_NATIVE_MAX = 500

# GIACT's real VerificationResponse values (from the sample API doc) -> a
# 0-10 band. Judgment call, not a documented vendor mapping -- see module
# docstring.
GIACT_VERIFICATION_RESPONSE_BAND: dict[str, float] = {
    "Pass": 9.0,
    "AcceptWithRisk": 6.0,
    "RiskAlert": 3.0,
    "NegativeData": 1.0,
}


def normalize_ews_score(overall_match_score: Optional[int]) -> Optional[float]:
    """EWS/AVS's real overallMatchScore is already 0-100 -- straight /10."""
    if overall_match_score is None:
        return None
    return round(overall_match_score / EWS_NATIVE_MAX * 10, 1)


def normalize_giact_response(verification_response: Optional[str]) -> Optional[float]:
    """Bands GIACT's real VerificationResponse code onto 0-10. Unrecognized
    values return None rather than guessing."""
    if verification_response is None:
        return None
    return GIACT_VERIFICATION_RESPONSE_BAND.get(verification_response)


def normalize_ekata_score(identity_check_score: Optional[int]) -> Optional[float]:
    """Ekata's identity_check_score is native 0-500 -- straight /50."""
    if identity_check_score is None:
        return None
    return round(identity_check_score / EKATA_NATIVE_MAX * 10, 1)


def vendor_code_outcome(normalized_score_0_10: Optional[float]) -> str:
    """Bands a normalized 0-10 score into the same verified/review/fail
    vocabulary engine/decision.py's domains use, so it can be injected as
    a 4th domain into the deterministic score (see pipeline.py) -- exactly
    "add it to our deterministic score" from the call."""
    if normalized_score_0_10 is None:
        return "fail"
    if normalized_score_0_10 >= 8:
        return "verified"
    if normalized_score_0_10 >= 4:
        return "review"
    return "fail"


VENDOR_CODE_RULE_ID = {
    "verified": "VENDOR_CODE_HIGH_CONFIDENCE",
    "review": "VENDOR_CODE_MEDIUM_CONFIDENCE",
    "fail": "VENDOR_CODE_LOW_CONFIDENCE",
}
