import json
import time
import asyncio
from datetime import datetime
from google import genai
from core.config import settings
from core.exceptions import DocumentValidationError, UnreadableDocumentError
from services.policy_loader import get_document_requirements
from db import supabase

MODEL = settings.gemini_model


class DocumentValidatorAgent:
    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def run(self, claim: dict, documents: list[dict]) -> None:
        start = time.time()

        category = claim.get("category", "")
        requirements = get_document_requirements(category)
        required_types = requirements.get("required", [])
        submitted_types = [doc.get("document_type") for doc in documents]

        missing = [t for t in required_types if t not in submitted_types]

        prompt = f"""You are an insurance document validator. Analyze the submitted documents for a {category} claim.

Required document types for this category: {json.dumps(required_types)}
Submitted document types: {json.dumps(submitted_types)}
Missing documents: {json.dumps(missing)}

Documents metadata:
{json.dumps(documents, indent=2)}

Check the following:
1. Are all required documents present?
2. Are any documents unreadable (is_readable=false)?
3. Do document types match what's expected?

Respond with JSON:
{{
  "valid": true/false,
  "missing_documents": [...],
  "unreadable_documents": [...],
  "issues": [...]
}}"""

        response = await asyncio.to_thread(self.client.models.generate_content, model=MODEL, contents=prompt)
        result_text = getattr(response, "text", None) or getattr(response, "response", None) or str(response)
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            start_idx = result_text.find("{")
            end_idx = result_text.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx:
                result = json.loads(result_text[start_idx:end_idx])
            else:
                result = {"valid": len(missing) == 0, "missing_documents": missing, "unreadable_documents": [], "issues": []}

        duration_ms = int((time.time() - start) * 1000)

        unreadable = [doc for doc in documents if not doc.get("is_readable", True)]
        if unreadable:
            self._write_trace("document_validator", "failed", duration_ms, error_message=f"Unreadable: {unreadable[0].get('file_path')}")
            raise UnreadableDocumentError(
                document_id=unreadable[0].get("document_id", "unknown"),
                file_name=unreadable[0].get("file_path", "unknown"),
            )

        if missing:
            details = {"missing": missing, "category": category, "submitted": submitted_types}
            self._write_trace("document_validator", "failed", duration_ms, error_message=f"Missing: {missing}")
            raise DocumentValidationError(
                message=f"Missing required documents for {category}: {missing}",
                details=details,
            )

        self._write_trace("document_validator", "passed", duration_ms, output=result)

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
