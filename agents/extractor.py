import json
import time
from datetime import datetime
from anthropic import AsyncAnthropic
from core.config import settings
from core.exceptions import ComponentFailureError
from db import supabase

MODEL = "claude-haiku-4-5-20251001"


class ExtractionAgent:
    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def run(self, documents: list[dict]) -> list[dict]:
        start = time.time()

        try:
            enriched = []
            for doc in documents:
                extracted = await self._extract_single(doc)
                enriched.append(extracted)

            duration_ms = int((time.time() - start) * 1000)
            self._write_trace("extractor", "passed", duration_ms, output={"documents_processed": len(enriched)})
            return enriched

        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            self._write_trace("extractor", "failed", duration_ms, error_message=str(e))
            raise ComponentFailureError("extractor", str(e))

    async def _extract_single(self, doc: dict) -> dict:
        doc_type = doc.get("document_type", "OTHER")
        file_path = doc.get("file_path", "unknown")

        prompt = f"""You are an insurance document data extractor. Extract structured data from a {doc_type} document.

Document metadata:
- Type: {doc_type}
- File: {file_path}
- Readable: {doc.get('is_readable', True)}

Based on the document type, extract the relevant fields:

For PRESCRIPTION: patient_name, doctor_name, hospital_name, date, diagnosis, medications (list with name, dosage, quantity), doctor_registration_number
For HOSPITAL_BILL: patient_name, hospital_name, date, items (list with description, amount), total_amount, payment_mode
For PHARMACY_BILL: patient_name, pharmacy_name, date, items (list with drug_name, quantity, amount, is_generic), total_amount
For LAB_REPORT: patient_name, lab_name, date, tests (list with test_name, result, reference_range)
For DIAGNOSTIC_REPORT: patient_name, center_name, date, test_type, findings, impression
For DISCHARGE_SUMMARY: patient_name, hospital_name, admission_date, discharge_date, diagnosis, procedures, treating_doctor
For DENTAL_REPORT: patient_name, dentist_name, date, procedures (list with name, tooth_number, cost), total_amount

Since this is a simulation without actual file content, generate realistic extracted data for a {doc_type} document.

Respond ONLY with a JSON object containing the extracted fields."""

        response = await self.client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        result_text = response.content[0].text
        try:
            extracted_data = json.loads(result_text)
        except json.JSONDecodeError:
            start_idx = result_text.find("{")
            end_idx = result_text.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx:
                extracted_data = json.loads(result_text[start_idx:end_idx])
            else:
                extracted_data = {"raw_text": result_text, "parse_failed": True}

        doc["extracted_data"] = extracted_data
        doc["confidence"] = 0.85 if extracted_data.get("parse_failed") else 0.95
        return doc

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
