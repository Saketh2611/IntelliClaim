import json
import time
import asyncio
from datetime import datetime
from google import genai
from core.config import settings
from core.exceptions import PatientMismatchError, ComponentFailureError
from db import supabase

MODEL = settings.gemini_model


class CrossDocValidatorAgent:
    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.client   = genai.Client(api_key=settings.gemini_api_key)

    async def run(self, claim: dict, documents: list[dict]) -> None:
        start = time.time()

        # only process docs that were successfully extracted
        extracted_docs = [
            {
                "document_type":  doc.get("document_type"),
                "extracted_data": doc.get("extracted_data", {}),
            }
            for doc in documents
            if doc.get("extracted_data") and doc.get("is_readable", True)
        ]

        if len(extracted_docs) < 2:
            self._write_trace(
                status          = "passed",
                input_snapshot  = {"doc_count": len(extracted_docs)},
                output_snapshot = {"reason": "fewer than 2 readable documents — skipping cross-validation"},
                duration_ms     = int((time.time() - start) * 1000),
            )
            return

        # ── Stage A: Deterministic name check first ───────────────────────
        name_mismatches = self._check_names_deterministic(extracted_docs)

        if name_mismatches:
            duration_ms = int((time.time() - start) * 1000)
            self._write_trace(
                status          = "failed",
                input_snapshot  = {"documents": extracted_docs},
                output_snapshot = {"mismatches": name_mismatches},
                error_message   = "Patient name mismatch across documents",
                duration_ms     = duration_ms,
            )
            raise PatientMismatchError(mismatches=name_mismatches)

        # ── Stage B: LLM check for fuzzy / deeper consistency ─────────────
        try:
            result = await self._llm_consistency_check(claim, extracted_docs)
        except Exception as e:
            # LLM failed — degrade, don't crash
            duration_ms = int((time.time() - start) * 1000)
            self._write_trace(
                status          = "degraded",
                input_snapshot  = {"documents": extracted_docs},
                output_snapshot = None,
                error_message   = f"LLM cross-validation failed: {str(e)}",
                duration_ms     = duration_ms,
            )
            return  # deterministic check passed — continue with reduced confidence

        duration_ms = int((time.time() - start) * 1000)

        mismatches      = result.get("mismatches", [])
        patient_issues  = [m for m in mismatches if m.get("field") == "patient_name"]
        other_issues    = [m for m in mismatches if m.get("field") != "patient_name"]

        if patient_issues:
            self._write_trace(
                status          = "failed",
                input_snapshot  = {"documents": extracted_docs},
                output_snapshot = {
                    "patient_mismatches": patient_issues,
                    "other_issues":       other_issues,
                    "notes":              result.get("notes"),
                },
                error_message   = "Patient name mismatch detected by LLM",
                duration_ms     = duration_ms,
            )
            raise PatientMismatchError(mismatches=patient_issues)

        # other issues (date, amount) — log in trace but don't stop pipeline
        self._write_trace(
            status          = "passed",
            input_snapshot  = {"documents": extracted_docs},
            output_snapshot = {
                "consistent":    result.get("consistent"),
                "other_issues":  other_issues,   # date/amount mismatches visible in trace
                "notes":         result.get("notes"),
            },
            duration_ms     = duration_ms,
        )

    # ── Deterministic name check ──────────────────────────────────────────

    def _check_names_deterministic(self, extracted_docs: list[dict]) -> list[dict]:
        """
        Extract patient_name from each doc.
        Flag if names are clearly different (not just formatting variations).
        """
        names = []
        for doc in extracted_docs:
            data = doc.get("extracted_data", {})
            name = (
                data.get("patient_name")
                or data.get("patient")
                or data.get("name")
            )
            if name:
                names.append({
                    "document_type": doc.get("document_type"),
                    "patient_name":  name.strip(),
                })

        if len(names) < 2:
            return []  # can't compare if fewer than 2 docs have names

        mismatches = []
        base = names[0]
        for other in names[1:]:
            if not self._names_match(base["patient_name"], other["patient_name"]):
                mismatches.append({
                    "field":      "patient_name",
                    "doc1_type":  base["document_type"],
                    "doc1_value": base["patient_name"],
                    "doc2_type":  other["document_type"],
                    "doc2_value": other["patient_name"],
                })

        return mismatches

    def _names_match(self, name1: str, name2: str) -> bool:
        """
        Fuzzy match — handles:
        - case differences: "rajesh kumar" vs "Rajesh Kumar"
        - initials: "R. Kumar" vs "Rajesh Kumar"
        - extra spaces
        """
        n1 = name1.lower().strip()
        n2 = name2.lower().strip()

        if n1 == n2:
            return True

        # check if last name matches (most reliable)
        parts1 = n1.split()
        parts2 = n2.split()
        if parts1 and parts2 and parts1[-1] == parts2[-1]:
            return True

        # check initial match: "R. Kumar" vs "Rajesh Kumar"
        if len(parts1) >= 1 and len(parts2) >= 1:
            if parts1[0].rstrip(".") == parts2[0][0]:
                return True
            if parts2[0].rstrip(".") == parts1[0][0]:
                return True

        return False

    # ── LLM consistency check ─────────────────────────────────────────────

    async def _llm_consistency_check(self, claim: dict, extracted_docs: list[dict]) -> dict:
        prompt = f"""You are an insurance cross-document validator.
Verify consistency across documents submitted for a single claim.

Claim details:
- Member ID: {claim.get('member_id')}
- Category: {claim.get('claim_category')}
- Treatment Date: {claim.get('treatment_date')}
- Claimed Amount: {claim.get('claimed_amount')}

Documents:
{json.dumps(extracted_docs, indent=2)}

Check:
1. Patient name — is it the same person across all documents?
2. Date — are treatment dates aligned (within 1 day is acceptable)?
3. Amount — does bill total match itemized line items?
4. Provider — do documents refer to the same hospital/clinic?

Respond ONLY with JSON:
{{
  "consistent": true/false,
  "mismatches": [
    {{
      "field": "patient_name|date|amount|provider",
      "doc1_type": "...",
      "doc1_value": "...",
      "doc2_type": "...",
      "doc2_value": "...",
      "severity": "HIGH|MEDIUM|LOW"
    }}
  ],
  "notes": "..."
}}"""

        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model    = MODEL,
            contents = prompt,
        )

        result_text = getattr(response, "text", None) or str(response)
        return self._parse_json(result_text)

    def _parse_json(self, text: str) -> dict:
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
        # parse failed — return neutral result, don't fake consistent=True
        raise ComponentFailureError("cross_doc_validator", "LLM response could not be parsed")

    def _write_trace(self, status: str, input_snapshot: dict,
                     output_snapshot: dict, duration_ms: int,
                     error_message: str = None):
        supabase.table("trace_steps").insert({
            "claim_id":        self.claim_id,
            "step_name":       "cross_doc_validation",
            "status":          status,
            "input_snapshot":  input_snapshot,
            "output_snapshot": output_snapshot,
            "error_message":   error_message,
            "duration_ms":     duration_ms,
            "created_at":      datetime.utcnow().isoformat(),
        }).execute()