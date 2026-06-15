import time
from datetime import datetime
from core.exceptions import DocumentValidationError
from services.policy_loader import get_document_requirements
from db import supabase


class DocumentValidatorAgent:
    """
    Stage 1 — pure deterministic logic, no LLM.
    Checks document types only, not content.
    Stops the pipeline immediately on failure.
    """

    def __init__(self, claim_id: str):
        self.claim_id = claim_id

    async def run(self, claim: dict, documents: list[dict]) -> None:
        start = time.time()

        # Bug 1 fix: correct key name
        category       = claim.get("claim_category", "")
        requirements   = get_document_requirements(category)
        required_types = requirements.get("required", [])
        optional_types = requirements.get("optional", [])
        submitted_types = [doc.get("document_type") for doc in documents]

        # ── Check 1: Missing required types
        missing = [t for t in required_types if t not in submitted_types]

        if missing:
            self._write_trace(
                status          = "failed",
                input_snapshot  = {
                    "claim_category": category,
                    "required":       required_types,
                    "submitted":      submitted_types,
                },
                output_snapshot = {"missing_types": missing},
                error_message   = f"Missing required documents: {missing}",
                duration_ms     = int((time.time() - start) * 1000),
            )
            raise DocumentValidationError(
                message = self._build_missing_message(category, submitted_types, missing, required_types),
                details = {
                    "claim_category":  category,
                    "required_types":  required_types,
                    "submitted_types": submitted_types,
                    "missing_types":   missing,
                }
            )

        # ── Check 2: Duplicate required types (Bug 2 fix)
        seen, duplicates = {}, []
        for doc in documents:
            t = doc.get("document_type")
            if t in required_types:
                if t in seen:
                    duplicates.append(t)
                seen[t] = True

        if duplicates:
            self._write_trace(
                status          = "failed",
                input_snapshot  = {
                    "claim_category": category,
                    "required":       required_types,
                    "submitted":      submitted_types,
                },
                output_snapshot = {"duplicate_types": duplicates},
                error_message   = f"Duplicate document types: {duplicates}",
                duration_ms     = int((time.time() - start) * 1000),
            )
            raise DocumentValidationError(
                message = self._build_duplicate_message(category, submitted_types, duplicates, required_types),
                details = {
                    "claim_category":  category,
                    "required_types":  required_types,
                    "submitted_types": submitted_types,
                    "duplicate_types": duplicates,
                }
            )

        # ── All checks passed
        self._write_trace(
            status          = "passed",
            input_snapshot  = {
                "claim_category": category,
                "required":       required_types,
                "submitted":      submitted_types,
            },
            output_snapshot = {"message": "All required documents present"},
            duration_ms     = int((time.time() - start) * 1000),
        )

    # ── Message builders ───────────────────────────────────────────────────

    def _build_missing_message(self, category, submitted_types, missing_types, required_types) -> str:
        return (
            f"Your {category.replace('_', ' ').title()} claim requires: "
            f"{', '.join(self._friendly(t) for t in required_types)}. "
            f"You uploaded: {', '.join(self._friendly(t) for t in submitted_types)}. "
            f"Please also provide: {', '.join(self._friendly(t) for t in missing_types)}."
        )

    def _build_duplicate_message(self, category, submitted_types, duplicates, required_types) -> str:
        return (
            f"You uploaded duplicate documents: {', '.join(self._friendly(t) for t in duplicates)}. "
            f"Your {category.replace('_', ' ').title()} claim requires exactly: "
            f"{', '.join(self._friendly(t) for t in required_types)}. "
            f"Please replace one {self._friendly(duplicates[0])} with the missing document."
        )

    def _friendly(self, doc_type: str) -> str:
        return {
            "PRESCRIPTION":      "Doctor's Prescription",
            "HOSPITAL_BILL":     "Hospital Bill",
            "PHARMACY_BILL":     "Pharmacy Bill",
            "LAB_REPORT":        "Lab Report",
            "DIAGNOSTIC_REPORT": "Diagnostic Report",
            "DISCHARGE_SUMMARY": "Discharge Summary",
            "DENTAL_REPORT":     "Dental Report",
        }.get(doc_type, doc_type)

    def _write_trace(self, status: str, input_snapshot: dict,
                     output_snapshot: dict, duration_ms: int,
                     error_message: str = None):
        supabase.table("trace_steps").insert({
            "claim_id":        self.claim_id,
            "step_name":       "document_validation",
            "status":          status,
            "input_snapshot":  input_snapshot,
            "output_snapshot": output_snapshot,
            "error_message":   error_message,
            "duration_ms":     duration_ms,
            "created_at":      datetime.utcnow().isoformat(),
        }).execute()