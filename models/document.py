from pydantic import BaseModel
from typing import Optional
from enum import Enum


class DocumentType(str, Enum):
    PRESCRIPTION      = "PRESCRIPTION"
    HOSPITAL_BILL     = "HOSPITAL_BILL"
    PHARMACY_BILL     = "PHARMACY_BILL"
    LAB_REPORT        = "LAB_REPORT"
    DIAGNOSTIC_REPORT = "DIAGNOSTIC_REPORT"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    DENTAL_REPORT     = "DENTAL_REPORT"
    OTHER             = "OTHER"


class ExtractedDocument(BaseModel):
    document_id:    str
    document_type:  DocumentType
    file_path:      str
    is_readable:    bool
    extracted_data: Optional[dict] = None   # structured LLM output
    confidence:     float = 1.0             # drops if doc is blurry/partial