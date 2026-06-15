import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from models.claim    import ClaimSubmitRequest, ClaimSubmitResponse
from models.decisions import DecisionResponse
from models.trace    import ClaimTraceResponse
from services.pipeline import ClaimPipeline
from api.dependencies  import verify_api_key
from core.config import settings
from db import supabase
from core.exceptions import (
    DocumentValidationError,
    UnreadableDocumentError,
    PatientMismatchError,
)

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


@router.post("/upload", response_model=ClaimSubmitResponse)
async def submit_claim_with_files(
    member_id: str = Form(...),
    policy_id: str = Form("PLUM_GHI_2024"),
    claim_category: str = Form(...),
    claimed_amount: float = Form(...),
    treatment_date: str = Form(...),
    hospital_name: str | None = Form(None),
    simulate_component_failure: bool = Form(False),
    document_types: str = Form(...),
    files: list[UploadFile] = File(...),
    _: str = Depends(verify_api_key),
):
    try:
        parsed_types = json.loads(document_types)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="document_types must be a JSON array")

    if not isinstance(parsed_types, list) or len(parsed_types) != len(files):
        raise HTTPException(
            status_code=422,
            detail="document_types must contain exactly one type for each uploaded file",
        )

    member = supabase.table("members").select("*").eq("member_id", member_id).single().execute()
    if not member.data:
        raise HTTPException(status_code=404, detail=f"Member {member_id} not found")

    claim_id = str(uuid.uuid4())
    supabase.table("claims").insert({
        "claim_id":       claim_id,
        "member_id":      member_id,
        "policy_id":      policy_id,
        "claim_category": claim_category,
        "claimed_amount": claimed_amount,
        "treatment_date": treatment_date,
        "hospital_name":  hospital_name,
        "status":         "pending",
    }).execute()

    upload_dir = Path(settings.upload_dir) / claim_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    documents = []
    db_documents = []
    for index, upload in enumerate(files):
        document_id = str(uuid.uuid4())
        original_name = Path(upload.filename or f"document_{index + 1}").name
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name)
        file_path = upload_dir / f"{document_id}_{safe_name}"
        file_path.write_bytes(await upload.read())

        document = {
            "file_id": document_id,
            "document_type": parsed_types[index],
            "file_path": str(file_path),
            "file_name": original_name,
            "mime_type": upload.content_type or "application/octet-stream",
        }
        documents.append(document)
        db_documents.append({
            "document_id": document_id,
            "claim_id": claim_id,
            "document_type": document["document_type"],
            "file_path": document["file_path"],
            "mime_type": document["mime_type"],
        })

    if db_documents:
        supabase.table("documents").insert(db_documents).execute()

    claim = {
        "member_id": member_id,
        "policy_id": policy_id,
        "claim_category": claim_category,
        "claimed_amount": claimed_amount,
        "treatment_date": treatment_date,
        "hospital_name": hospital_name,
        "simulate_component_failure": simulate_component_failure,
    }

    try:
        pipeline = ClaimPipeline(claim_id, simulate_component_failure)
        await pipeline.run(claim=claim, documents=documents)
    except DocumentValidationError as e:
        raise HTTPException(status_code=422, detail={"error": "DOCUMENT_VALIDATION", **e.details})
    except UnreadableDocumentError as e:
        raise HTTPException(status_code=422, detail={"error": "UNREADABLE_DOCUMENT", "file_name": e.file_name})
    except PatientMismatchError as e:
        raise HTTPException(status_code=422, detail={"error": "PATIENT_MISMATCH", "mismatches": e.mismatches})

    return ClaimSubmitResponse(
        claim_id=claim_id,
        status="completed",
        message="Claim processed successfully",
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
