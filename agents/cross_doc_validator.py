import json
import time
from datetime import datetime
from anthropic import AsyncAnthropic
from core.config import settings
from core.exceptions import PatientMismatchError
from db import supabase

MODEL = "claude-haiku-4-5-20251001"


class CrossDocValidatorAgent:
    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def run(self, claim: dict, documents: list[dict]) -> None:
        start = time.time()

        extracted_docs = []
        for doc in documents:
            if doc.get("extracted_data"):
                extracted_docs.append({
                    "document_type": doc.get("document_type"),
                    "extracted_data": doc.get("extracted_data"),
                })

        if len(extracted_docs) < 2:
            duration_ms = int((time.time() - start) * 1000)
            self._write_trace("cross_doc_validator", "passed", duration_ms, output={"reason": "fewer than 2 documents with extracted data"})
            return

        prompt = f"""You are an insurance cross-document validator. Your job is to verify consistency across multiple documents submitted for a single insurance claim.

Claim details:
- Member ID: {claim.get('member_id')}
- Patient Name: {claim.get('patient_name', 'unknown')}
- Category: {claim.get('category')}
- Treatment Date: {claim.get('treatment_date', 'unknown')}

Documents with extracted data:
{json.dumps(extracted_docs, indent=2)}

Check for:
1. Patient name consistency — is the same patient named across all documents?
2. Date consistency — are treatment dates reasonably aligned?
3. Hospital/provider consistency — do documents refer to the same provider when expected?
4. Amount consistency — does the bill total match itemized amounts?

Respond with JSON:
{{
  "consistent": true/false,
  "mismatches": [
    {{
      "field": "patient_name",
      "doc1_type": "PRESCRIPTION",
      "doc1_value": "...",
      "doc2_type": "HOSPITAL_BILL",
      "doc2_value": "..."
    }}
  ],
  "notes": "..."
}}"""

        response = await self.client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        result_text = response.content[0].text
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            start_idx = result_text.find("{")
            end_idx = result_text.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx:
                result = json.loads(result_text[start_idx:end_idx])
            else:
                result = {"consistent": True, "mismatches": [], "notes": "parse_failed"}

        duration_ms = int((time.time() - start) * 1000)

        if not result.get("consistent", True) and result.get("mismatches"):
            patient_mismatches = [m for m in result["mismatches"] if m.get("field") == "patient_name"]
            if patient_mismatches:
                self._write_trace("cross_doc_validator", "failed", duration_ms, error_message="Patient name mismatch across documents")
                raise PatientMismatchError(mismatches=patient_mismatches)

        self._write_trace("cross_doc_validator", "passed", duration_ms, output=result)

    def _write_trace(self, step_name: str, status: str, duration_ms: int, output: dict = None, error_message: str = None):
        supabase.table("trace_steps").insert({
            "claim_id": self.claim_id,
            "step_name": step_name,
            "status": status,
            "duration_ms": duration_ms,
            "output_snapshot": output,
            "error_message": error_message,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
