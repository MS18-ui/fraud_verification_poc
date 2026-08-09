"""
Run: python run_demo.py
Loads tests/sample_data/partial_match.json, maps it to canonical form,
runs the full rule pipeline, and prints the decision payload that would
be handed to the LLM Explainability step.
"""
import json
from datetime import datetime, timezone

from engine.canonical_mapper import build_canonical
from engine.decision import run_decision


def main():
    with open("tests/sample_data/partial_match.json") as f:
        sample = json.load(f)

    entity = sample["entity"]
    giact_result = sample["vendors"]["giact"]["response"]["PostInquiryResult"]
    ekata_response = sample["vendors"]["ekata"]["response"]

    submitted = {
        "first_name": entity["first_name"],
        "last_name": entity["last_name"],
        "address_line1": entity["address_line1"],
        "city": entity["city"],
        "state": entity["state"],
        "zip_code": entity["zip_code"],
        "phone": entity["phone_number"],
        "date_of_birth": entity["date_of_birth"],
    }

    canonical = build_canonical(
        unique_id=sample["unique_id"],
        submitted=submitted,
        giact_result=giact_result,
        ekata_response=ekata_response,
        ews_response=None,
        account_holder_type="person",
        reference_time=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )

    result = run_decision(canonical)

    print("=== Canonical (excerpt) ===")
    print(json.dumps({
        "identity": canonical["identity"],
        "owner": canonical["owner"],
        "account": canonical["account"],
    }, indent=2, default=str))

    print("\n=== Decision Result ===")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
