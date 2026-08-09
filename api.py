"""
api.py

Thin FastAPI wrapper around pipeline.py -- this is the "hosted live site"
entry point (see build guide Section 12 for the Azure deployment mapping:
API Management in front of this, running on Container Apps/AKS).
"""

from __future__ import annotations
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from pipeline import run_pipeline

app = FastAPI(title="Payee Verification Engine")


class VerificationRequest(BaseModel):
    unique_id: str
    account_holder_type: str = "person"
    first_name: str | None = None
    last_name: str | None = None
    ssn: str | None = None
    dob: str | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    phone: str | None = None
    email: str | None = None
    routing_number: str | None = None
    account_number: str | None = None


@app.post("/v1/verify")
def verify(req: VerificationRequest):
    try:
        payload = req.model_dump(exclude_none=True)
        account_holder_type = payload.pop("account_holder_type", "person")
        result = run_pipeline(payload, account_holder_type=account_holder_type)
        return {
            "unique_id": result["unique_id"],
            "decision": result["composite"]["final_decision"],
            "composite_score": result["composite"]["composite_score"],
            "reason_codes": result["composite"]["reason_codes"],
            "explanation": result["explanation"]["explanation"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
