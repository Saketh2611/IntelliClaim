from pydantic import BaseModel, Field
from typing import Optional
from datetime import date
from enum import Enum


class ClaimCategory(str, Enum):
    CONSULTATION        = "CONSULTATION"
    PHARMACY            = "PHARMACY"
    DENTAL              = "DENTAL"
    DIAGNOSTIC          = "DIAGNOSTIC"
    VISION              = "VISION"
    ALTERNATIVE_MEDICINE = "ALTERNATIVE_MEDICINE"


class ClaimStatus(str, Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    COMPLETED  = "completed"
    FAILED     = "failed"


class DocumentUpload(BaseModel):
    file_id:       str
    document_type: str             # what member says it is
    file_path:     str             # local path after upload
    mime_type:     str


class ClaimSubmitRequest(BaseModel):
    member_id:      str
    policy_id:      str = "PLUM_GHI_2024"
    claim_category: ClaimCategory
    claimed_amount: float = Field(..., gt=0)
    treatment_date: date
    hospital_name:  Optional[str] = None
    documents:      list[DocumentUpload]
    
    # TC011: simulate component failure for testing
    simulate_component_failure: bool = False


class ClaimSubmitResponse(BaseModel):
    claim_id:   str
    status:     ClaimStatus
    message:    str