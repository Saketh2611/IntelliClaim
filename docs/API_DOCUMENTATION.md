# IntelliClaim API Documentation

## Overview

IntelliClaim exposes a RESTful API built with FastAPI for automated health insurance claims processing. All endpoints (except `/health`) require API key authentication via the `X-API-Key` header.

**Base URL:** `http://localhost:8000` (direct) or `http://localhost/api` (via Nginx)

**Authentication:** All protected endpoints require the header:
```
X-API-Key: <your-api-key>
```

The API key is configured via the `AI_API_KEY` environment variable.

---

## Endpoints

### 1. Health Check

```
GET /health
```

Checks API and database connectivity. No authentication required.

**Response (200 OK):**
```json
{
  "status": "ok",
  "db": "connected",
  "version": "1.0.0"
}
```

If the database is unreachable:
```json
{
  "status": "ok",
  "db": "error: <error message>",
  "version": "1.0.0"
}
```

---

### 2. Submit Claim (JSON)

```
POST /api/v1/claims
```

Submit a claim with document references (file paths already uploaded to disk).

**Request Headers:**
```
Content-Type: application/json
X-API-Key: <your-api-key>
```

**Request Body:**
```json
{
  "member_id": "EMP001",
  "policy_id": "PLUM_GHI_2024",
  "claim_category": "CONSULTATION",
  "claimed_amount": 1500.00,
  "treatment_date": "2024-11-15",
  "hospital_name": "Apollo Hospitals",
  "documents": [
    {
      "file_id": "uuid-1",
      "document_type": "PRESCRIPTION",
      "file_path": "/path/to/prescription.pdf",
      "mime_type": "application/pdf"
    },
    {
      "file_id": "uuid-2",
      "document_type": "HOSPITAL_BILL",
      "file_path": "/path/to/bill.pdf",
      "mime_type": "application/pdf"
    }
  ],
  "simulate_component_failure": false
}
```

**Field Descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `member_id` | string | Yes | Employee/dependent ID (e.g., EMP001, DEP001) |
| `policy_id` | string | No | Defaults to `PLUM_GHI_2024` |
| `claim_category` | enum | Yes | One of: `CONSULTATION`, `PHARMACY`, `DENTAL`, `DIAGNOSTIC`, `VISION`, `ALTERNATIVE_MEDICINE` |
| `claimed_amount` | float | Yes | Amount claimed in INR (must be > 0) |
| `treatment_date` | date | Yes | Date of treatment (YYYY-MM-DD) |
| `hospital_name` | string | No | Hospital/clinic name (used for network discount) |
| `documents` | array | Yes | Array of document objects |
| `simulate_component_failure` | bool | No | Testing flag to simulate pipeline degradation |

**Document Object:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file_id` | string | Yes | Unique ID for the document |
| `document_type` | string | Yes | One of: `PRESCRIPTION`, `HOSPITAL_BILL`, `LAB_REPORT`, `DIAGNOSTIC_REPORT`, `PHARMACY_BILL`, `DENTAL_REPORT`, `DISCHARGE_SUMMARY` |
| `file_path` | string | Yes | Path to the file on disk |
| `mime_type` | string | Yes | MIME type (e.g., `application/pdf`, `image/jpeg`) |

**Success Response (200 OK):**
```json
{
  "claim_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "message": "Claim processed successfully"
}
```

**Error Responses:**

*Document Validation Error (422):*
```json
{
  "detail": {
    "error": "DOCUMENT_VALIDATION",
    "missing_documents": ["PRESCRIPTION"],
    "message": "Missing required documents for CONSULTATION category"
  }
}
```

*Unreadable Document (422):*
```json
{
  "detail": {
    "error": "UNREADABLE_DOCUMENT",
    "file_name": "blurry_receipt.jpg"
  }
}
```

*Patient Mismatch (422):*
```json
{
  "detail": {
    "error": "PATIENT_MISMATCH",
    "mismatches": [
      {
        "field": "patient_name",
        "document_1": "Rajesh Kumar",
        "document_2": "Suresh Kumar"
      }
    ]
  }
}
```

*Member Not Found (404):*
```json
{
  "detail": "Member EMP999 not found"
}
```

---

### 3. Submit Claim with File Upload

```
POST /api/v1/claims/upload
```

Submit a claim with actual file uploads (multipart form data). This is the endpoint used by the frontend.

**Request Headers:**
```
Content-Type: multipart/form-data
X-API-Key: <your-api-key>
```

**Form Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `member_id` | string | Yes | Employee/dependent ID |
| `policy_id` | string | No | Defaults to `PLUM_GHI_2024` |
| `claim_category` | string | Yes | Claim category enum value |
| `claimed_amount` | float | Yes | Amount in INR |
| `treatment_date` | string | Yes | Date string (YYYY-MM-DD) |
| `hospital_name` | string | No | Hospital name |
| `simulate_component_failure` | bool | No | Testing flag |
| `document_types` | string | Yes | JSON array of document types, one per file (e.g., `["PRESCRIPTION", "HOSPITAL_BILL"]`) |
| `files` | file[] | Yes | One or more file uploads |

**Important:** The `document_types` JSON array must have exactly one entry per uploaded file, in the same order.

**Example (cURL):**
```bash
curl -X POST http://localhost:8000/api/v1/claims/upload \
  -H "X-API-Key: your-key" \
  -F "member_id=EMP001" \
  -F "claim_category=CONSULTATION" \
  -F "claimed_amount=1500" \
  -F "treatment_date=2024-11-15" \
  -F "hospital_name=Apollo Hospitals" \
  -F 'document_types=["PRESCRIPTION", "HOSPITAL_BILL"]' \
  -F "files=@prescription.pdf" \
  -F "files=@hospital_bill.pdf"
```

**Response:** Same as JSON submission endpoint.

---

### 4. Get Claim Details

```
GET /api/v1/claims/{claim_id}
```

Retrieve full claim data along with the decision (if processing is complete).

**Response (200 OK):**
```json
{
  "claim": {
    "claim_id": "550e8400-e29b-41d4-a716-446655440000",
    "member_id": "EMP001",
    "policy_id": "PLUM_GHI_2024",
    "claim_category": "CONSULTATION",
    "claimed_amount": 1500.00,
    "treatment_date": "2024-11-15",
    "hospital_name": "Apollo Hospitals",
    "status": "completed",
    "submitted_at": "2024-11-15T10:30:00Z"
  },
  "decision": {
    "decision_id": "dec-uuid",
    "outcome": "APPROVED",
    "approved_amount": 1200.00,
    "reason": "Claim approved with network discount applied.",
    "confidence": 0.95,
    "breakdown": [...],
    "network_discount_applied": 300.00,
    "copay_deducted": 0.00,
    "decided_at": "2024-11-15T10:30:05Z"
  }
}
```

---

### 5. Get Claim Decision

```
GET /api/v1/claims/{claim_id}/decision
```

Retrieve only the decision for a processed claim.

**Response (200 OK):**
```json
{
  "decision_id": "dec-uuid",
  "outcome": "APPROVED",
  "approved_amount": 1200.00,
  "reason": "Claim approved. Network discount of 20% applied at Apollo Hospitals.",
  "confidence": 0.95,
  "breakdown": [
    {
      "description": "Consultation fee",
      "amount": 1500.00,
      "status": "APPROVED",
      "reason": "Within sub-limit of INR 2000"
    }
  ],
  "network_discount_applied": 300.00,
  "copay_deducted": 120.00,
  "decided_at": "2024-11-15T10:30:05Z"
}
```

**Decision Outcomes:**

| Outcome | Meaning |
|---------|---------|
| `APPROVED` | Claim fully approved |
| `PARTIAL` | Claim partially approved (some items rejected or limits applied) |
| `REJECTED` | Claim rejected due to policy violations |
| `MANUAL_REVIEW` | Sent for human review (low confidence, fraud risk, or degraded pipeline) |

---

### 6. Get Claim Trace

```
GET /api/v1/claims/{claim_id}/trace
```

Retrieve the full execution trace showing every processing step.

**Response (200 OK):**
```json
{
  "claim_id": "550e8400-e29b-41d4-a716-446655440000",
  "steps": [
    {
      "step_name": "document_validator",
      "status": "passed",
      "input_snapshot": { "document_count": 2, "category": "CONSULTATION" },
      "output_snapshot": { "valid": true },
      "error_message": null,
      "duration_ms": 12,
      "created_at": "2024-11-15T10:30:00Z"
    },
    {
      "step_name": "extractor",
      "status": "passed",
      "input_snapshot": { "documents": 2 },
      "output_snapshot": { "extracted_fields": 8 },
      "error_message": null,
      "duration_ms": 3200,
      "created_at": "2024-11-15T10:30:01Z"
    }
  ],
  "total_steps": 6,
  "failed_steps": 0
}
```

**Trace Step Statuses:**

| Status | Meaning |
|--------|---------|
| `passed` | Step completed successfully |
| `failed` | Step failed critically (pipeline stopped) |
| `degraded` | Step failed but pipeline continued with reduced confidence |
| `skipped` | Step was not executed |

---

### 7. Get Member

```
GET /api/v1/members/{member_id}
```

Retrieve member information.

**Response (200 OK):**
```json
{
  "member_id": "EMP001",
  "name": "Rajesh Kumar",
  "date_of_birth": "1985-03-15",
  "gender": "M",
  "relationship": "SELF",
  "join_date": "2024-04-01",
  "policy_id": "PLUM_GHI_2024"
}
```

---

## Document Requirements by Category

Each claim category requires specific documents:

| Category | Required Documents | Optional Documents |
|----------|-------------------|-------------------|
| CONSULTATION | PRESCRIPTION, HOSPITAL_BILL | LAB_REPORT, DIAGNOSTIC_REPORT |
| DIAGNOSTIC | PRESCRIPTION, LAB_REPORT, HOSPITAL_BILL | DISCHARGE_SUMMARY |
| PHARMACY | PRESCRIPTION, PHARMACY_BILL | — |
| DENTAL | HOSPITAL_BILL | PRESCRIPTION, DENTAL_REPORT |
| VISION | PRESCRIPTION, HOSPITAL_BILL | — |
| ALTERNATIVE_MEDICINE | PRESCRIPTION, HOSPITAL_BILL | — |

---

## Policy Limits

| Category | Sub-Limit (INR) | Co-pay % | Network Discount % |
|----------|-----------------|----------|-------------------|
| Consultation | 2,000 | 10% | 20% |
| Diagnostic | 10,000 | 0% | 10% |
| Pharmacy | 15,000 | 0% (30% for branded) | — |
| Dental | 10,000 | 0% | — |
| Vision | 5,000 | 0% | — |
| Alternative Medicine | 8,000 | 0% | — |

- **Per-claim limit:** INR 5,000
- **Annual OPD limit:** INR 50,000
- **Minimum claim amount:** INR 500
- **Sum insured per employee:** INR 5,00,000

---

## Processing Pipeline

When a claim is submitted, it passes through 6 sequential agents:

```
1. Document Validator  →  2. Extractor  →  3. Cross-Doc Validator
         ↓                      ↓                    ↓
4. Policy Checker  →  5. Fraud Detector  →  6. Decision Maker
```

| Stage | Agent | Critical? | Failure Behavior |
|-------|-------|-----------|-----------------|
| 1 | Document Validator | Yes | Stops pipeline, returns specific error |
| 2 | Extractor | Yes | Degrades confidence by 0.3, continues |
| 3 | Cross-Doc Validator | Yes | Stops if patient names mismatch |
| 4 | Policy Checker | No | Degrades confidence by 0.25, continues |
| 5 | Fraud Detector | No | Degrades confidence by 0.1, continues |
| 6 | Decision Maker | Always | Always runs with available data |

---

## Error Codes

| HTTP Code | Error Type | Description |
|-----------|-----------|-------------|
| 401 | Unauthorized | Invalid or missing API key |
| 404 | Not Found | Member or claim not found |
| 422 | DOCUMENT_VALIDATION | Missing or duplicate required documents |
| 422 | UNREADABLE_DOCUMENT | Document could not be parsed by AI |
| 422 | PATIENT_MISMATCH | Patient names differ across documents |
| 500 | Internal Server Error | Unexpected pipeline failure |

---

## Network Hospitals

Claims from these hospitals receive network discounts:

- Apollo Hospitals
- Fortis Healthcare
- Max Healthcare
- Manipal Hospitals
- Narayana Health
- Medanta
- Kokilaben Dhirubhai Ambani Hospital
- Aster CMI Hospital
- Columbia Asia
- Sakra World Hospital

---

## Registered Members (Test Data)

| Member ID | Name | Join Date | Dependents |
|-----------|------|-----------|------------|
| EMP001 | Rajesh Kumar | 2024-04-01 | DEP001, DEP002 |
| EMP002 | Priya Singh | 2024-04-01 | — |
| EMP003 | Amit Verma | 2024-04-01 | DEP003 |
| EMP004 | Sneha Reddy | 2024-04-01 | — |
| EMP005 | Vikram Joshi | 2024-09-01 | — |
| EMP006 | Kavita Nair | 2024-04-01 | — |
| EMP007 | Suresh Patil | 2024-04-01 | DEP004, DEP005 |
| EMP008 | Ravi Menon | 2024-04-01 | — |
| EMP009 | Anita Desai | 2024-04-01 | — |
| EMP010 | Deepak Shah | 2024-04-01 | DEP006 |

---

## Rate Limits and Constraints

- **Max file upload size:** 50MB (Nginx limit)
- **API timeout:** 120 seconds (for AI processing)
- **Minimum claim amount:** INR 500
- **Claim submission deadline:** 30 days from treatment date
- **Same-day claims limit:** 2 (triggers fraud detection)
- **Monthly claims limit:** 6 (triggers fraud detection)
