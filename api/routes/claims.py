from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from models.claim    import ClaimSubmitRequest, ClaimSubmitResponse
from models.decision import DecisionResponse
from models.trace    import ClaimTraceResponse
from services.pipeline import ClaimPipeline
from services.storage  import save_upload
from api.dependencies  import verify_api_key
from db import supabase
from core.exceptions import (
    DocumentValidationError,
    UnreadableDocumentError,
    PatientMismatchError,
)
import uuid

router = APIRouter(prefix="/claims", tags=["Claims"])


@router.post("", response_model=ClaimSubmitResponse)
async def submit_claim(
    request: ClaimSubmitRequest,
    _: str = Depends(verify_api_key),
):
    # 1. verify member exists
    member = supabase.table("members").select("*").eq("member_id", request.member_id).single().execute()
    if not member.data:
        raise HTTPException(status_code=404, detail=f"Member {request.member_id} not found")

    # 2. create claim record
    claim_id = str(uuid.uuid4())
    supabase.table("claims").insert({
        "claim_id":       claim_id,
        "member_id":      request.member_id,
        "policy_id":      request.policy_id,
        "claim_category": request.claim_category,
        "claimed_amount": request.claimed_amount,
        "treatment_date": str(request.treatment_date),
        "hospital_name":  request.hospital_name,
        "status":         "pending",
    }).execute()

    # 3. run pipeline
    try:
        pipeline = ClaimPipeline(claim_id, request.simulate_component_failure)
        await pipeline.run(
            claim     = request.model_dump(),
            documents = [d.model_dump() for d in request.documents],
        )
    except DocumentValidationError as e:
        raise HTTPException(status_code=422, detail={"error": "DOCUMENT_VALIDATION", **e.details})
    except UnreadableDocumentError as e:
        raise HTTPException(status_code=422, detail={"error": "UNREADABLE_DOCUMENT", "file_name": e.file_name})
    except PatientMismatchError as e:
        raise HTTPException(status_code=422, detail={"error": "PATIENT_MISMATCH", "mismatches": e.mismatches})

    return ClaimSubmitResponse(
        claim_id = claim_id,
        status   = "completed",
        message  = "Claim processed successfully",
    )


@router.get("/{claim_id}", response_model=dict)
async def get_claim(claim_id: str, _: str = Depends(verify_api_key)):
    claim = supabase.table("claims").select("*").eq("claim_id", claim_id).single().execute()
    if not claim.data:
        raise HTTPException(status_code=404, detail="Claim not found")

    decision = supabase.table("decisions").select("*").eq("claim_id", claim_id).execute()

    return {
        "claim":    claim.data,
        "decision": decision.data[0] if decision.data else None,
    }


@router.get("/{claim_id}/trace", response_model=ClaimTraceResponse)
async def get_trace(claim_id: str, _: str = Depends(verify_api_key)):
    steps = (
        supabase.table("trace_steps")
        .select("*")
        .eq("claim_id", claim_id)
        .order("created_at")
        .execute()
    )
    if not steps.data:
        raise HTTPException(status_code=404, detail="No trace found for this claim")

    failed = [s for s in steps.data if s["status"] in ("failed", "degraded")]

    return ClaimTraceResponse(
        claim_id     = claim_id,
        steps        = steps.data,
        total_steps  = len(steps.data),
        failed_steps = len(failed),
    )


@router.get("/{claim_id}/decision", response_model=DecisionResponse)
async def get_decision(claim_id: str, _: str = Depends(verify_api_key)):
    decision = supabase.table("decisions").select("*").eq("claim_id", claim_id).single().execute()
    if not decision.data:
        raise HTTPException(status_code=404, detail="No decision yet for this claim")
    return decision.data