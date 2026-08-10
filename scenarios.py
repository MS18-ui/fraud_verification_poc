"""
Fraud typologies as risk profiles, plus response-code pools corrected against
the Integration Guide.

Two jobs:

1. **Correct the code pools.** The mock's own pools drifted from the vendor
   docs — `ACCOUNT_RESPONSE_CODES` carries `_1121`/`_9999` (in neither the
   integration guide's table nor the sandbox list) while omitting eight real
   codes, and `CUSTOMER_CODES_GIDENTIFY` carries undocumented `CI31`/`CI41`.
   `CONSUMER_ALERT_CODES` used invented `CA01`-`CA08` keys that collide with
   real *CustomerResponseCode* values; the real alert codes are numeric.

2. **Bias outcomes by typology.** Each profile is a set of *weight overrides*,
   never a deterministic tell. A label that is perfectly predictable from one
   field teaches a model nothing — every profile below keeps substantial
   overlap with `clean`, so a classifier has to combine signals to separate
   them. If a trivial model scores ~1.0 AUC on the output, these weights are
   too sharp and should be softened.

Source of truth: `docs/references/integration-guide.md`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ── Corrected code pools (Integration Guide) ────────────────────────────────

#: AccountResponseCode -> the VerificationResponse it implies. All 18 documented
#: codes. Numeric codes keep the XML enum's leading underscore, as the samples
#: render them (`_1111`).
ACCOUNT_CODE_OUTCOMES: Dict[str, str] = {
    "_1111": "Pass",
    "_2222": "Pass",
    "_3333": "Pass",
    "_5555": "Pass",
    "ND00": "Pass",                    # Informational
    "ND01": "NoData",
    "GS01": "Declined",
    "GS02": "Declined",
    "GS03": "Declined",
    "GS04": "Declined",
    "GN01": "NegativeData",
    "GN05": "Declined",                # UnassignedRoutingNumber
    "GP01": "PrivateBadChecksList",
    "RT00": "Declined",
    "RT01": "Declined",
    "RT02": "RejectItem",
    "RT03": "AcceptWithRisk",
    "RT04": "PassNdd",
}

#: CustomerResponseCode -> outcome, per the guide's CustomerResponseCode table.
#: CA* codes come from gAuthenticate, CI* from gIdentify.
CUSTOMER_CODE_OUTCOMES: Dict[str, str] = {
    "CA11": "Pass", "CI11": "Pass", "ND02": "Pass",
    "CA23": "AcceptWithRisk", "CI23": "AcceptWithRisk",   # address mismatch
    "CA24": "AcceptWithRisk", "CI24": "AcceptWithRisk",   # phone mismatch
    "CA25": "AcceptWithRisk", "CI25": "AcceptWithRisk",   # DOB / ID mismatch
    "CA21": "RiskAlert", "CI21": "RiskAlert",             # name mismatch
    "CA22": "RiskAlert", "CI22": "RiskAlert",             # TaxId mismatch
    "CA30": "RiskAlert", "CI30": "RiskAlert",             # multiple secondary
    "CA01": "Declined", "CI01": "Declined",
}

#: ConsumerAlertMessage codes — numeric, per the guide. The `{Input / Consumer
#: Record}` placeholder in the source table is resolved at generation time.
CONSUMER_ALERT_CODES: Dict[int, str] = {
    101: "{subject} address is a mail receiving/forwarding service and/or general delivery address",
    102: "{subject} address is a hotel, motel, campsite, or temporary residence",
    103: "{subject} address is a credit correction service",
    104: "{subject} address is a secretarial service",
    105: "{subject} address is a check cashing service",
    106: "{subject} address is a restaurant, bar, or nightclub",
    107: "{subject} address is a storage facility",
    108: "{subject} address is an airport or airfield",
    109: "{subject} address is a truck stop",
    110: "{subject} address is a correctional institution",
    111: "{subject} address is a hospital, clinic, or nursing home",
    112: "{subject} address is commercial",
    113: "{subject} address is institutional",
    114: "{subject} address is governmental",
    115: "{subject} address is a US Post Office",
    116: "Input zip code has not been issued",
    117: "Input zip code is military",
    118: "Input zip code is a PO box",
    119: "Input zip code is not valid for input city or state",
    120: "{subject} address reported as suspicious, misused, or used in fraud",
    121: "{subject} address is a multi-unit building reported as suspicious/misused/fraud",
    130: "Input address matches a previous address on the consumer record",
    201: "{subject} phone number is an answering service",
    202: "{subject} phone number is a public or pay phone",
    203: "{subject} phone number is commercial",
    204: "{subject} phone number is institutional",
    205: "{subject} phone number is governmental",
    220: "{subject} phone number reported as suspicious, misused, or used in fraud",
    300: "Input SSN is invalid or reported as not assigned",
    301: "Input SSN assigned before input Date of Birth",
    320: "{subject} SSN reported as suspicious or belonging to a minor",
    321: "{subject} SSN reported as misused or used in fraud",
    322: "{subject} SSN reported as deceased",
    400: "Input Driver's License format is invalid",
    420: "Input DL associated with additional person(s) not in MatchedPersonData",
    501: "Active duty alert on file",
    520: "{subject} address, SSN, and/or phone reported together in suspected misuse or fraud",
    521: "Consumer statement relates to true name fraud or credit fraud",
    522: "Initial fraud alert on record",
    523: "Extended fraud victim alert on record",
    524: "New consumer file under review; investigation suggested",
    525: "Consumer first and last names transposed",
    530: "Consumer Record access is currently restricted",
    531: "Consumer Record does not have a value for SSN",
    532: "Consumer Record does not have a value for Date of Birth",
    533: "Consumer Record does not have a value for Phone Number",
}

#: Alerts a healthy payee book still throws — benign, high-frequency ones.
BENIGN_ALERTS: Tuple[int, ...] = (112, 118, 130, 203, 531, 533)


def alert_message(code: int, rng: Optional[random.Random] = None) -> Dict[str, str]:
    """One ConsumerAlertMessage entry, with the {subject} placeholder resolved."""
    rnd = rng or random
    subject = rnd.choice(["Input", "Consumer record"])
    return {
        "Code": str(code),
        "Details": CONSUMER_ALERT_CODES[code].format(subject=subject),
    }


# ── EWS code pools ───────────────────────────────────────────────────────────
# The EWS account-owner-verification mock has no vendor spec yet; these codes
# are modeled on Early Warning Services conventions and documented in
# docs/references/ews-account-verification.md. Outcomes reuse the GIACT
# outcome vocabulary so data.match_flags.OUTCOME_TIER applies unchanged.

#: EWS accountStatus code -> (description, outcome).
EWS_ACCOUNT_STATUS_CODES: Dict[str, Tuple[str, str]] = {
    "01": ("Account open and in good standing", "Pass"),
    "02": ("Account closed", "Declined"),
    "03": ("Account has NSF or overdraft history", "AcceptWithRisk"),
    "04": ("Account dormant or inactive", "AcceptWithRisk"),
    "05": ("No information found for account", "NoData"),
    "06": ("Account not eligible for verification", "Declined"),
    "07": ("Participant reported fraud on account", "RiskAlert"),
}

#: Participant-reported condition codes attached to risky accounts.
EWS_CONDITION_CODES: Dict[str, str] = {
    "P1": "Account closed for cause by participant",
    "P2": "Suspected fraud activity reported on account",
    "P3": "Stop-payment history on account",
    "P4": "Account inactive or dormant for 12+ months",
    "P5": "Excessive returned items on account",
    "P6": "Account ownership recently changed",
}

#: Status codes legal for each match flag (0 complete / 1 partial / 2 no
#: match), so a flagged response's account story agrees with its tier.
EWS_STATUS_BY_TIER: Dict[int, Tuple[str, ...]] = {
    0: ("01",),
    1: ("01", "03", "04"),
    2: ("02", "05", "06", "07"),
}


# ── Risk profiles ───────────────────────────────────────────────────────────

Weights = Dict[str, int]


@dataclass(frozen=True)
class RiskProfile:
    """
    Weight overrides describing how one typology's responses tend to look.

    Every field is a *tendency*. `clean` and each fraud profile deliberately
    overlap so the resulting labels are learnable but not trivially separable.
    """

    name: str
    #: AccountResponseCode weights (gVerify / gAuthenticate).
    account_codes: Weights
    #: CustomerResponseCode weights, gIdentify (CI*) and gAuthenticate (CA*).
    customer_codes_gidentify: Weights
    customer_codes_gauthenticate: Weights
    #: SsnStatus weights on matched person records.
    ssn_statuses: Weights = field(
        default_factory=lambda: {"clear": 92, "issued-recently": 5,
                                 "suspicious": 2, "deceased": 1}
    )
    #: Consumer alert codes this typology tends to raise, and how many.
    alert_codes: Tuple[int, ...] = BENIGN_ALERTS
    n_alerts: Tuple[int, int] = (0, 2)
    #: How many records the bureau matches. Thin files are themselves a signal.
    n_matched_records: Tuple[int, int] = (1, 4)
    #: P(the first matched record carries the submitted name).
    echo_input_identity: float = 0.9
    #: P(the input address appears as the record's Current address).
    address_match: float = 0.85
    #: Age of the account on file, in days.
    account_age_days: Tuple[int, int] = (400, 4000)
    #: Days since the account record last changed.
    account_updated_days: Tuple[int, int] = (30, 900)
    #: P(an OFAC scan returns a potential match).
    ofac_hit_chance: float = 0.01
    #: Number of address records held on file.
    n_addresses: Tuple[int, int] = (1, 4)
    n_phones: Tuple[int, int] = (1, 3)
    # ── Identity-risk ranges ──
    # Clean and fraud ranges deliberately OVERLAP: a legitimate payee can score
    # mid-range and a fraudster can score low, so the score is a strong but
    # imperfect signal. Disjoint ranges would leak the label (single-feature
    # AUC ~1.0), which is worse than no label.
    #: Identity risk on a 0-1000 scale (higher = riskier). The Ekata generator
    #: inverts it into `identity_check_score`, where higher = safer.
    identity_risk_range: Tuple[int, int] = (10, 470)
    #: Per-element risk scores (ipRiskScore, emailRisk) on a 0-1 scale.
    element_risk_range: Tuple[float, float] = (0.02, 0.55)


#: A legitimate, established payee: mostly Pass, thick file, stable account.
CLEAN = RiskProfile(
    name="clean",
    account_codes={"_1111": 60, "_2222": 12, "_3333": 8, "_5555": 6,
                   "ND00": 6, "RT04": 3, "RT03": 2, "ND01": 2, "GS01": 1},
    customer_codes_gidentify={"CI11": 78, "ND02": 8, "CI23": 6, "CI24": 3,
                              "CI25": 2, "CI21": 1, "CI22": 1, "CI01": 1},
    customer_codes_gauthenticate={"CA11": 80, "CA23": 7, "CA24": 4, "CA25": 3,
                                  "CA21": 2, "CA22": 2, "CA01": 2},
)

#: Fabricated identity — real SSN, invented name/DOB. The tell is a thin,
#: recently-created file with elements that do not corroborate each other.
SYNTHETIC_IDENTITY = RiskProfile(
    name="synthetic_identity",
    account_codes={"_1111": 30, "_2222": 6, "ND00": 12, "ND01": 22,
                   "RT03": 10, "RT00": 8, "GS01": 6, "GN01": 6},
    customer_codes_gidentify={"CI11": 20, "CI21": 22, "CI22": 20, "CI25": 14,
                              "CI30": 8, "ND02": 10, "CI01": 6},
    customer_codes_gauthenticate={"CA11": 22, "CA21": 24, "CA22": 20,
                                  "CA25": 14, "CA30": 10, "CA01": 10},
    ssn_statuses={"clear": 45, "issued-recently": 40, "suspicious": 14,
                  "deceased": 1},
    alert_codes=(300, 301, 320, 524, 531, 532, 116, 119),
    n_alerts=(1, 4),
    n_matched_records=(0, 2),          # thin file
    echo_input_identity=0.45,
    address_match=0.35,
    account_age_days=(5, 400),
    account_updated_days=(1, 120),
    n_addresses=(0, 2),
    n_phones=(0, 2),
    identity_risk_range=(300, 800),
    element_risk_range=(0.25, 0.88),
)

#: A real person's identity used without their knowledge. The file is thick and
#: healthy — it is the *input* that disagrees with it, especially the address.
IDENTITY_THEFT = RiskProfile(
    name="identity_theft",
    account_codes={"_1111": 34, "_2222": 8, "ND00": 8, "RT03": 14,
                   "RT00": 10, "RT01": 8, "GS01": 10, "ND01": 8},
    customer_codes_gidentify={"CI11": 22, "CI23": 30, "CI24": 16, "CI21": 14,
                              "CI25": 8, "ND02": 6, "CI01": 4},
    customer_codes_gauthenticate={"CA11": 22, "CA23": 30, "CA24": 16,
                                  "CA21": 14, "CA25": 10, "CA01": 8},
    alert_codes=(101, 102, 120, 130, 220, 321, 520, 521, 522, 523),
    n_alerts=(1, 4),
    n_matched_records=(2, 6),          # thick genuine file
    echo_input_identity=0.75,
    address_match=0.10,                # input address is the giveaway
    account_age_days=(300, 4000),
    account_updated_days=(1, 90),
    n_addresses=(2, 6),
    n_phones=(1, 4),
    identity_risk_range=(270, 730),
    element_risk_range=(0.22, 0.82),
)

#: Legitimate account, hijacked. Account and contact details changed recently.
ACCOUNT_TAKEOVER = RiskProfile(
    name="account_takeover",
    account_codes={"_1111": 40, "_2222": 8, "ND00": 8, "RT03": 14,
                   "RT01": 10, "RT00": 8, "GS02": 6, "ND01": 6},
    customer_codes_gidentify={"CI11": 26, "CI24": 26, "CI23": 18, "CI25": 12,
                              "CI21": 8, "ND02": 6, "CI01": 4},
    customer_codes_gauthenticate={"CA11": 24, "CA24": 26, "CA23": 18,
                                  "CA25": 12, "CA21": 10, "CA01": 10},
    alert_codes=(201, 202, 220, 120, 130, 522, 524, 533),
    n_alerts=(1, 3),
    n_matched_records=(2, 5),
    echo_input_identity=0.85,
    address_match=0.55,
    account_age_days=(500, 4000),      # long-standing account...
    account_updated_days=(0, 21),      # ...changed in the last three weeks
    n_addresses=(2, 5),
    n_phones=(2, 4),
    identity_risk_range=(250, 710),
    element_risk_range=(0.20, 0.78),
)

#: Funnel account for moving proceeds. Brand new, thin, often flagged already.
MULE_ACCOUNT = RiskProfile(
    name="mule_account",
    account_codes={"_1111": 22, "ND00": 8, "ND01": 14, "RT00": 16,
                   "RT01": 12, "RT02": 10, "GS01": 8, "GN01": 6, "GP01": 4},
    customer_codes_gidentify={"CI11": 24, "CI23": 20, "CI30": 16, "CI21": 12,
                              "CI22": 10, "CI01": 10, "ND02": 8},
    customer_codes_gauthenticate={"CA11": 22, "CA23": 20, "CA30": 16,
                                  "CA21": 12, "CA22": 10, "CA01": 20},
    ssn_statuses={"clear": 70, "issued-recently": 18, "suspicious": 11,
                  "deceased": 1},
    alert_codes=(101, 105, 118, 120, 121, 201, 220, 520, 530),
    n_alerts=(1, 3),
    n_matched_records=(0, 3),
    echo_input_identity=0.60,
    address_match=0.40,
    account_age_days=(0, 90),          # opened weeks ago
    account_updated_days=(0, 45),
    n_addresses=(0, 3),
    n_phones=(0, 2),
    identity_risk_range=(320, 830),
    element_risk_range=(0.28, 0.92),
)

#: BEC / vendor impersonation — a payee posing as a known supplier, usually
#: with a freshly registered entity and a mismatched bank account.
BUSINESS_IMPERSONATION = RiskProfile(
    name="business_impersonation",
    account_codes={"_1111": 26, "_5555": 6, "ND00": 8, "ND01": 14,
                   "RT03": 12, "RT00": 10, "GS01": 10, "GN05": 8, "GN01": 6},
    customer_codes_gidentify={"CI11": 20, "CI21": 26, "CI23": 18, "CI22": 14,
                              "CI30": 10, "ND02": 6, "CI01": 6},
    customer_codes_gauthenticate={"CA11": 18, "CA21": 28, "CA23": 18,
                                  "CA22": 14, "CA30": 10, "CA01": 12},
    alert_codes=(112, 101, 103, 104, 119, 120, 203, 524),
    n_alerts=(1, 3),
    n_matched_records=(0, 2),
    echo_input_identity=0.35,          # name does not match the registration
    address_match=0.30,
    account_age_days=(0, 240),
    account_updated_days=(0, 60),
    n_addresses=(0, 2),
    n_phones=(0, 2),
    identity_risk_range=(290, 770),
    element_risk_range=(0.25, 0.84),
)

#: Sanctions exposure. The OFAC hit dominates and forces RiskAlert.
OFAC_WATCHLIST = RiskProfile(
    name="ofac_watchlist",
    account_codes={"_1111": 40, "_2222": 10, "ND00": 10, "ND01": 12,
                   "RT03": 10, "GS01": 10, "GN01": 8},
    customer_codes_gidentify={"CI11": 34, "CI21": 22, "CI23": 16, "CI30": 12,
                              "ND02": 10, "CI01": 6},
    customer_codes_gauthenticate={"CA11": 32, "CA21": 24, "CA23": 16,
                                  "CA30": 12, "CA01": 16},
    alert_codes=(120, 520, 521, 530),
    n_alerts=(0, 2),
    n_matched_records=(1, 4),
    echo_input_identity=0.80,
    address_match=0.60,
    ofac_hit_chance=0.85,
    identity_risk_range=(340, 900),
    element_risk_range=(0.20, 0.72),
)

PROFILES: Dict[str, RiskProfile] = {
    p.name: p for p in (
        CLEAN, SYNTHETIC_IDENTITY, IDENTITY_THEFT, ACCOUNT_TAKEOVER,
        MULE_ACCOUNT, BUSINESS_IMPERSONATION, OFAC_WATCHLIST,
    )
}

#: Overall fraud base rate. ~1.5% is a realistic order of magnitude for a
#: commercial payee book being screened at onboarding; any given institution's
#: true rate is unknown to us and should be recalibrated against its actuals.
FRAUD_BASE_RATE = 0.015

#: Share of the fraudulent population by typology.
TYPOLOGY_SHARES: Dict[str, int] = {
    "synthetic_identity": 30,
    "identity_theft": 25,
    "account_takeover": 15,
    "mule_account": 15,
    "business_impersonation": 10,
    "ofac_watchlist": 5,
}

#: Typologies that only make sense for a business payee, and for a person.
BUSINESS_ONLY_TYPOLOGIES = frozenset({"business_impersonation"})
PERSON_ONLY_TYPOLOGIES = frozenset({"synthetic_identity", "identity_theft"})


def pick_typology(is_business: bool, rng: Optional[random.Random] = None) -> str:
    """Choose a fraud typology appropriate to the payee's entity type."""
    rnd = rng or random
    excluded = PERSON_ONLY_TYPOLOGIES if is_business else BUSINESS_ONLY_TYPOLOGIES
    choices = {k: v for k, v in TYPOLOGY_SHARES.items() if k not in excluded}
    return rnd.choices(list(choices), weights=list(choices.values()), k=1)[0]


def weighted(weights: Weights, rng: Optional[random.Random] = None) -> str:
    """Draw one key from a `{value: weight}` mapping."""
    rnd = rng or random
    return rnd.choices(list(weights), weights=list(weights.values()), k=1)[0]


def profile_for(name: Optional[str]) -> RiskProfile:
    """Look up a profile by name, defaulting to `clean` for unknown names."""
    return PROFILES.get(name or "clean", CLEAN)
