import base64
import json
import time
import asyncio
import os
from datetime import datetime
from google import genai
from google.genai import types as genai_types
from core.config import settings
from core.exceptions import ComponentFailureError, UnreadableDocumentError
from db import supabase

MODEL = settings.gemini_model

# minimum required fields per document type for readability check
REQUIRED_FIELDS = {
    "PRESCRIPTION":      ["patient_name", "doctor_name", "diagnosis"],
    "HOSPITAL_BILL":     ["patient_name", "total_amount", "items"],
    "PHARMACY_BILL":     ["patient_name", "total_amount", "items"],
    "LAB_REPORT":        ["patient_name", "tests"],
    "DIAGNOSTIC_REPORT": ["patient_name", "findings"],
    "DISCHARGE_SUMMARY": ["patient_name", "diagnosis"],
    "DENTAL_REPORT":     ["patient_name", "procedures"],
}


class ExtractionAgent:
    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def run(self, documents: list[dict]) -> list[dict]:
        enriched = []

        for doc in documents:
            start = time.time()
            doc_type = doc.get("document_type", "OTHER")
            file_path = doc.get("file_path", "")
            file_name = doc.get("file_name", file_path)

            try:
                extracted_data, confidence = await self._extract_single(doc)

                # check if critical fields are present
                required = REQUIRED_FIELDS.get(doc_type, [])
                missing_fields = [f for f in required if not extracted_data.get(f)]
                is_readable = len(missing_fields) == 0

                # if critical fields missing entirely → unreadable
                if not is_readable and len(missing_fields) >= len(required):
                    duration_ms = int((time.time() - start) * 1000)
                    self._write_trace(
                        step_name       = f"extraction_{doc_type.lower()}",
                        status          = "failed",
                        input_snapshot  = {"file_path": file_path, "doc_type": doc_type},
                        output_snapshot = {"missing_fields": missing_fields},
                        error_message   = f"Document unreadable — missing: {missing_fields}",
                        duration_ms     = duration_ms,
                    )
                    raise UnreadableDocumentError(
                        document_id = doc.get("file_id", "unknown"),
                        file_name   = file_name,
                    )

                # partial readability — reduce confidence
                if missing_fields:
                    confidence = max(0.4, confidence - 0.1 * len(missing_fields))

                doc["extracted_data"] = extracted_data
                doc["is_readable"]    = is_readable
                doc["confidence"]     = round(confidence, 2)

                # update DB
                self._update_document_in_db(doc)

                duration_ms = int((time.time() - start) * 1000)
                self._write_trace(
                    step_name       = f"extraction_{doc_type.lower()}",
                    status          = "passed",
                    input_snapshot  = {"file_path": file_path, "doc_type": doc_type},
                    output_snapshot = {
                        "extracted_fields": list(extracted_data.keys()),
                        "missing_fields":   missing_fields,
                        "confidence":       confidence,
                    },
                    duration_ms     = duration_ms,
                )
                enriched.append(doc)

            except UnreadableDocumentError:
                raise  # bubble up — pipeline must stop and ask user to re-upload

            except Exception as e:
                # individual doc extraction failed but not unreadable
                # degrade gracefully — mark low confidence, continue
                duration_ms = int((time.time() - start) * 1000)
                self._write_trace(
                    step_name       = f"extraction_{doc_type.lower()}",
                    status          = "degraded",
                    input_snapshot  = {"file_path": file_path, "doc_type": doc_type},
                    output_snapshot = None,
                    error_message   = str(e),
                    duration_ms     = duration_ms,
                )
                doc["extracted_data"] = {}
                doc["is_readable"]    = False
                doc["confidence"]     = 0.2
                enriched.append(doc)

        return enriched

    async def _extract_single(self, doc: dict) -> tuple[dict, float]:
        doc_type  = doc.get("document_type", "OTHER")
        file_path = doc.get("file_path", "")
        mime_type = doc.get("mime_type", "image/jpeg")

        # ── Read actual file ──────────────────────────────────────────────
        file_content = None
        confidence   = 0.95

        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as f:
                raw_bytes    = f.read()
                file_content = base64.b64encode(raw_bytes).decode("utf-8")
        else:
            # file doesn't exist — for test cases without real files
            # use any content provided directly in the doc dict
            confidence = 0.75

        prompt = self._build_prompt(doc_type)

        # ── Call LLM with actual file if available ────────────────────────
        if file_content:
            contents = [
                genai_types.Part.from_bytes(
                    data     = base64.b64decode(file_content),
                    mime_type = mime_type,
                ),
                prompt,
            ]
        else:
            # no file — extract from any structured content in the doc
            inline_content = doc.get("content") or doc.get("extracted_data") or {}
            contents = [
                f"{prompt}\n\nDocument content (structured):\n{json.dumps(inline_content, indent=2)}"
            ]

        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model    = MODEL,
            contents = contents,
        )

        result_text = getattr(response, "text", None) or str(response)
        extracted   = self._parse_json(result_text)

        return extracted, confidence

    def _build_prompt(self, doc_type: str) -> str:
        field_map = {
            "PRESCRIPTION": """
Extract exactly these fields (use null if not found, do NOT invent values):
- patient_name (string)
- doctor_name (string)
- doctor_registration (string, format: STATE/NUMBER/YEAR)
- hospital_name (string)
- date (string, DD-MM-YYYY)
- diagnosis (string)
- medications (list of {name, dosage, frequency, duration})
- tests_ordered (list of strings)""",

            "HOSPITAL_BILL": """
Extract exactly these fields (use null if not found, do NOT invent values):
- patient_name (string)
- hospital_name (string)
- bill_number (string)
- date (string, DD-MM-YYYY)
- items (list of {description, amount})
- subtotal (number)
- total_amount (number)
- payment_mode (string)""",

            "PHARMACY_BILL": """
Extract exactly these fields (use null if not found, do NOT invent values):
- patient_name (string)
- pharmacy_name (string)
- drug_license_number (string)
- date (string)
- prescribing_doctor (string)
- items (list of {drug_name, quantity, mrp, amount, is_generic})
- total_amount (number)""",

            "LAB_REPORT": """
Extract exactly these fields (use null if not found, do NOT invent values):
- patient_name (string)
- lab_name (string)
- sample_date (string)
- report_date (string)
- referring_doctor (string)
- tests (list of {test_name, result, unit, normal_range, is_abnormal})
- pathologist_name (string)""",

            "DENTAL_REPORT": """
Extract exactly these fields (use null if not found, do NOT invent values):
- patient_name (string)
- dentist_name (string)
- date (string)
- procedures (list of {name, tooth_number, amount})
- total_amount (number)""",
        }

        fields = field_map.get(doc_type, "Extract all relevant medical fields as key-value pairs.")

        return f"""You are a medical document extraction system for Indian health insurance claims.
Extract ONLY what is visible in the document. Do NOT invent or hallucinate any values.
If a field is not visible or legible, set it to null.

Document type: {doc_type}
{fields}

Respond ONLY with a valid JSON object. No explanation, no markdown fences."""

    def _parse_json(self, text: str) -> dict:
        # strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end   = text.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
        return {"parse_failed": True, "raw_text": text[:500]}

    def _update_document_in_db(self, doc: dict):
        file_id = doc.get("file_id")
        if not file_id:
            return
        try:
            supabase.table("documents").update({
                "extracted_data": doc.get("extracted_data"),
                "is_readable":    doc.get("is_readable"),
            }).eq("document_id", file_id).execute()
        except Exception:
            pass  # don't crash pipeline on DB update failure

    def _write_trace(self, step_name: str, status: str,
                     input_snapshot: dict, output_snapshot: dict,
                     duration_ms: int, error_message: str = None):
        supabase.table("trace_steps").insert({
            "claim_id":        self.claim_id,
            "step_name":       step_name,
            "status":          status,
            "input_snapshot":  input_snapshot,
            "output_snapshot": output_snapshot,
            "error_message":   error_message,
            "duration_ms":     duration_ms,
            "created_at":      datetime.utcnow().isoformat(),
        }).execute()