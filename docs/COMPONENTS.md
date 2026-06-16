# IntelliClaim Component Documentation

This document explains every component of the IntelliClaim system in detail.

---

## Table of Contents

1. [Backend Components](#backend-components)
   - [Entry Point (main.py)](#entry-point)
   - [API Layer](#api-layer)
   - [Agent Pipeline](#agent-pipeline)
   - [Services](#services)
   - [Models](#models)
   - [Database Layer](#database-layer)
   - [Core Configuration](#core-configuration)
2. [Frontend Components](#frontend-components)
3. [Infrastructure](#infrastructure)
4. [Data Files](#data-files)

---

## Backend Components

### Entry Point

**File:** `main.py`

The FastAPI application initialization. Creates the app instance with metadata, adds CORS middleware (allows all origins for development), and mounts three route groups:

- `/health` — Health check (no prefix)
- `/api/v1/claims` — Claim submission and retrieval
- `/api/v1/members` — Member lookup

---

### API Layer

#### `api/dependencies.py` — Authentication

Provides the `verify_api_key` dependency that protects all endpoints. Reads the `X-API-Key` header and compares it against the `AI_API_KEY` environment variable. Returns 401 if the key doesn't match.

#### `api/routes/health.py` — Health Check

**GET /health** — Returns API status and tests database connectivity by querying the members table. Used by load balancers and monitoring.

#### `api/routes/claims.py` — Claims API

The main API file with 5 endpoints:

| Endpoint | Purpose |
|----------|---------|
| `POST /claims` | Submit claim via JSON (programmatic use) |
| `POST /claims/upload` | Submit claim with file uploads (frontend use) |
| `GET /claims/{id}` | Get claim data + decision |
| `GET /claims/{id}/trace` | Get processing trace |
| `GET /claims/{id}/decision` | Get decision only |

**JSON submission flow:**
1. Verify member exists in database
2. Create claim record with status "pending"
3. Run the 6-agent pipeline
4. Return claim_id and status

**File upload flow:**
1. Parse `document_types` JSON array
2. Verify member exists
3. Create claim record
4. Save uploaded files to `uploads/{claim_id}/{document_id}_{filename}`
5. Insert document records in database
6. Run the pipeline
7. Return claim_id and status

Both endpoints catch specific pipeline exceptions and map them to structured 422 errors.

#### `api/routes/members.py` — Members API

**GET /members/{member_id}** — Simple lookup returning all member fields from Supabase.

---

### Agent Pipeline

The heart of IntelliClaim. Six specialized agents process claims sequentially, each responsible for one aspect of validation/decision-making.

#### `agents/document_validator.py` — Stage 1: Document Validator

**Type:** Fully deterministic (no AI)

**Purpose:** Ensures all required documents are present for the claim category before any AI processing begins.

**Logic:**
1. Loads document requirements from `policy_terms.json` by category
2. Extracts document types from the submitted documents array
3. Checks all required types are present
4. Checks for duplicate document types (only one of each allowed)
5. Writes a trace step with pass/fail status

**Failure mode:** Raises `DocumentValidationError` with details about missing/duplicate documents. This stops the pipeline immediately.

**Why it's first:** No point running expensive AI extraction if basic documents are missing.

---

#### `agents/extractor.py` — Stage 2: Extraction Agent

**Type:** AI-powered (Google Gemini)

**Purpose:** Reads uploaded documents (PDFs, images) and extracts structured data fields.

**Logic:**
1. For each document, reads the file from disk
2. Base64-encodes the content for the Gemini API
3. Sends document to Gemini with type-specific extraction prompts:
   - PRESCRIPTION: patient_name, doctor_name, diagnosis, medications, date
   - HOSPITAL_BILL: patient_name, hospital_name, total_amount, line_items, date
   - LAB_REPORT: patient_name, test_name, results, date
   - etc.
4. Parses Gemini's JSON response
5. Checks extracted fields against required fields for that document type
6. Calculates a readability confidence (0.4-1.0) based on how many fields were extracted
7. If ALL required fields are missing, raises `UnreadableDocumentError`
8. Writes trace step with extracted data summary

**Failure mode:** If the Gemini API fails entirely, raises `ComponentFailureError` — pipeline continues with 0.3 confidence penalty.

---

#### `agents/cross_doc_validator.py` — Stage 3: Cross-Document Validator

**Type:** Deterministic + AI assistance

**Purpose:** Verifies consistency across all submitted documents — primarily that the patient name is the same on every document.

**Logic:**

*Stage A — Deterministic Name Matching:*
1. Collects `patient_name` from each extracted document
2. Normalizes names (lowercase, strip extra whitespace)
3. Compares using:
   - Exact match
   - Initials handling (e.g., "R. Kumar" matches "Rajesh Kumar")
   - Fuzzy matching for minor variations
4. If names differ significantly → raises `PatientMismatchError`

*Stage B — LLM Consistency Check:*
1. Sends all extracted data to Gemini
2. Asks for date consistency, amount consistency, provider name consistency
3. Mismatches in dates/amounts are logged as warnings but don't stop the pipeline

**Failure mode:** `PatientMismatchError` stops the pipeline (critical — could indicate fraud or wrong documents).

---

#### `agents/policy_checker.py` — Stage 4: Policy Checker

**Type:** Primarily deterministic + AI for diagnosis mapping

**Purpose:** Validates the claim against all policy rules, calculates approved amount, applies deductions.

**Checks performed (in order):**

1. **Minimum claim amount** — Must be ≥ INR 500
2. **Initial waiting period** — Member must have been covered for 30+ days
3. **Condition-specific waiting periods** — Uses Gemini to map the extracted diagnosis to a known condition (e.g., "Type 2 Diabetes Mellitus" → "diabetes" → 90-day wait)
4. **Exclusions** — Checks if the condition/procedure appears in the exclusions list
5. **Pre-authorization** — Flags if MRI/CT/PET scan without pre-auth
6. **Per-claim limit** — Caps at INR 5,000
7. **Category sub-limits** — Caps at category-specific limit (e.g., INR 2,000 for consultation)
8. **Line-item approval** — For dental/vision, checks each item against covered/excluded procedures
9. **Network hospital discount** — Applies if hospital name matches network list
10. **Co-pay deduction** — Applies percentage-based co-pay after other calculations
11. **High-value flagging** — Flags claims ≥ INR 25,000 for review

**Output:** Returns a `policy_result` dict with:
- `eligible` (bool)
- `approved_amount` (float)
- `rejection_reason` (string, if rejected)
- `flags` (array of warning flags)
- `notes` (array of applied adjustments)
- `network_discount_applied` (float)
- `copay_deducted` (float)

**Failure mode:** Raises `ComponentFailureError` — pipeline continues with 0.25 penalty.

---

#### `agents/fraud_detector.py` — Stage 5: Fraud Detector

**Type:** Deterministic scoring + AI pattern analysis

**Purpose:** Scores the claim for fraud risk based on behavioral patterns and anomalies.

**Deterministic Scoring Rules:**

| Pattern | Fraud Score | Trigger |
|---------|------------|---------|
| High-value claim | 0.65 | Amount ≥ INR 25,000 |
| Same-day frequency | 0.75 | > 2 claims same day |
| Monthly frequency | 0.70 | > 6 claims in a month |
| Repeated amounts | 0.55 | ≥ 3 claims with identical amount |
| High-value non-network | 0.80 | ≥ 25k at non-network hospital |

**AI Enhancement:**
- Sends claim details + member history to Gemini
- Asks for pattern analysis (unusual timing, suspicious document patterns)
- Merges AI score with deterministic score (takes maximum)

**Output:**
- `fraud_score` (0.0 - 1.0)
- `risk_level` ("low", "medium", "high")
- `requires_manual_review` (bool, true if score ≥ 0.8)
- `flags` (array of triggered rules)

**Failure mode:** Raises `ComponentFailureError` — pipeline continues with 0.1 penalty.

---

#### `agents/decision_maker.py` — Stage 6: Decision Maker

**Type:** Deterministic logic + AI for explanation writing

**Purpose:** Makes the final claim decision and writes it to the database.

**Decision Logic (deterministic):**

```
IF confidence < 0.5:
    → MANUAL_REVIEW ("Low confidence due to processing issues")

IF degraded OR confidence < 0.7:
    → MANUAL_REVIEW ("Pipeline degradation requires human review")

IF fraud_score >= 0.8:
    → MANUAL_REVIEW ("High fraud risk detected")

IF policy says ineligible:
    → REJECTED (with policy checker's reason)

IF 0 < approved_amount < claimed_amount:
    → PARTIAL (with explanation of adjustments)

IF eligible AND approved_amount > 0:
    → APPROVED

ELSE:
    → REJECTED
```

**AI Role:** Only used to polish the reason text into clear, human-readable language. The decision itself is never changed by AI.

**Database Write:** Inserts into `decisions` table with:
- `decision_id`, `claim_id`, `outcome`, `approved_amount`
- `reason`, `breakdown` (line items), `confidence`, `decided_at`

---

### Services

#### `services/pipeline.py` — Pipeline Orchestrator

The `ClaimPipeline` class coordinates all six agents. Key responsibilities:

- **Confidence tracking** — Starts at 1.0, reduced by failed components
- **Failed component tracking** — Records which agents failed
- **Graceful degradation** — Non-critical failures logged but don't stop processing
- **Status updates** — Updates claim status in DB (pending → processing → completed/failed)
- **Trace writing** — Writes degradation trace steps

**Simulate failure mode:** When `simulate_component_failure=True`, the policy checker is forced to fail (for testing graceful degradation).

#### `services/policy_loader.py` — Policy Configuration Loader

Loads and parses `policy_terms.json` at startup. Provides accessor methods for:
- Document requirements by category
- Coverage limits
- Waiting periods
- Exclusions lists
- Network hospital names
- Fraud thresholds

---

### Models

Pydantic models defining the data contracts:

#### `models/claim.py`

- **ClaimCategory** (enum) — CONSULTATION, PHARMACY, DENTAL, DIAGNOSTIC, VISION, ALTERNATIVE_MEDICINE
- **ClaimStatus** (enum) — pending, processing, completed, failed
- **DocumentUpload** — file_id, document_type, file_path, mime_type
- **ClaimSubmitRequest** — Full request body with all claim fields + documents array
- **ClaimSubmitResponse** — claim_id, status, message

#### `models/decisions.py`

- **DecisionOutcome** (enum) — APPROVED, PARTIAL, REJECTED, MANUAL_REVIEW
- **LineItemDecision** — description, amount, status, reason
- **DecisionResponse** — Full decision with outcome, amount, reason, confidence, breakdown, discounts

#### `models/trace.py`

- **TraceStep** — step_name, status, input/output snapshots, error, duration, timestamp
- **ClaimTraceResponse** — claim_id, steps array, total_steps, failed_steps count

---

### Database Layer

#### `db/client.py`

Initializes the Supabase client using credentials from settings. Exports a single `supabase` client instance used throughout the application.

#### `db/seed.py`

Database seeding script that populates the `members` table with test data from `policy_terms.json`. Run with:
```bash
python -m db.seed
```

**Database Tables:**

| Table | Purpose | Key Fields |
|-------|---------|-----------|
| `members` | Employee/dependent roster | member_id, name, join_date, policy_id |
| `claims` | Submitted claims | claim_id, member_id, category, amount, status |
| `documents` | Uploaded document metadata | document_id, claim_id, type, file_path, extracted_data |
| `decisions` | Final claim decisions | decision_id, claim_id, outcome, approved_amount, reason |
| `trace_steps` | Processing audit trail | claim_id, step_name, status, input/output, duration |

---

### Core Configuration

#### `core/config.py` — Settings

Uses `pydantic-settings` to load environment variables:

| Setting | Env Variable | Default | Description |
|---------|-------------|---------|-------------|
| `supabase_url` | SUPABASE_URL | required | Supabase project URL |
| `supabase_service_role_key` | SUPABASE_SERVICE_ROLE_KEY | None | Supabase service key |
| `supabase_key` | SUPABASE_KEY | None | Legacy key name (fallback) |
| `gemini_api_key` | GEMINI_API_KEY | None | Google Gemini API key |
| `gemini_model` | — | gemini-2.5-flash | Gemini model to use |
| `ai_api_key` | AI_API_KEY | None | API auth key for this service |
| `upload_dir` | — | uploads | File upload directory |
| `policy_path` | — | policy_terms.json | Path to policy config |
| `environment` | — | development | Runtime environment |

#### `core/exceptions.py` — Custom Exceptions

| Exception | Used By | Stops Pipeline? |
|-----------|---------|----------------|
| `DocumentValidationError` | Document Validator | Yes |
| `UnreadableDocumentError` | Extractor | Yes |
| `PatientMismatchError` | Cross-Doc Validator | Yes |
| `ComponentFailureError` | Any non-critical agent | No (degrades) |
| `PolicyLoadError` | Policy Loader | Yes (startup) |

---

## Frontend Components

### `frontend/src/App.jsx` — Main Application

A single-page React application (619 lines) with four main sections:

#### 1. Claim Submission Form (Left Panel)

**State management:** React `useState` hooks for all form fields.

**Features:**
- Dynamic document requirements based on selected category
- Multi-file upload with drag-and-drop
- Document type selector per uploaded file
- Form validation before submission
- "Simulate component failure" checkbox for testing
- Loading state during submission

**API call:** Constructs a `FormData` object and POSTs to `/api/v1/claims/upload`.

#### 2. Decision Display (Right Panel - Top)

**Shows after successful submission:**
- Color-coded outcome badge (green/yellow/red/blue)
- Approved amount vs. claimed amount
- Confidence percentage with visual indicator
- Reason text from the AI
- Line-item breakdown table (if available)
- Network discount and co-pay amounts

#### 3. Request Preview (Right Panel - Middle)

Displays the raw JSON that was sent to the API, formatted for readability. Useful for debugging and understanding exactly what the system received.

#### 4. Trace Timeline (Bottom Panel)

**Shows the processing pipeline execution:**
- Each of the 6 steps displayed as a timeline entry
- Status icons: checkmark (passed), X (failed), warning (degraded)
- Step name and duration in milliseconds
- Expandable sections showing:
  - Input snapshot (what data the step received)
  - Output snapshot (what the step produced)
  - Error message (if failed/degraded)

**Error handling:** Normalizes different error response formats (DOCUMENT_VALIDATION, UNREADABLE_DOCUMENT, PATIENT_MISMATCH) into user-friendly messages displayed in a red error banner.

### `frontend/src/App.css` — Styling

CSS modules with:
- Responsive grid layout (form left, results right)
- Color-coded status badges
- Animated loading states
- Expandable/collapsible sections
- Drag-and-drop file upload zone styling

### `frontend/Dockerfile`

Multi-stage build:
1. Build stage: Node.js builds the React app with Vite
2. Production stage: Nginx serves the static bundle

---

## Infrastructure

### `docker-compose.yml` — Service Orchestration

Three services on a bridge network:

| Service | Image | Internal Port | Role |
|---------|-------|--------------|------|
| nginx | nginx:alpine | 80 (exposed) | Reverse proxy |
| frontend | custom build | 80 (internal) | Static React app |
| backend | custom build | 8000 (internal) | FastAPI server |

### `nginx/nginx.conf` — Reverse Proxy

Routes:
- `/ ` → Frontend container (React app)
- `/api/` → Backend container (FastAPI)

Settings:
- Client max body size: 50MB (for file uploads)
- Proxy read timeout: 120s (for AI processing time)
- Proxy connect timeout: 10s

### `Dockerfile` (Backend)

- Base: `python:3.12-slim`
- Installs requirements, copies source
- Runs: `uvicorn main:app --host 0.0.0.0 --port 8000`

---

## Data Files

### `policy_terms.json` — Policy Configuration

The complete insurance policy definition. Sections:

| Section | Content |
|---------|---------|
| `policy_id` / `policy_name` | Policy identification |
| `policy_holder` | Company info, dates, employee count |
| `coverage` | Sum insured, OPD limit, per-claim limit, family floater |
| `opd_categories` | Per-category sub-limits, co-pay, discounts, covered procedures |
| `waiting_periods` | Initial + condition-specific waiting periods |
| `exclusions` | Conditions, dental, and vision exclusions |
| `pre_authorization` | Procedures requiring pre-auth |
| `network_hospitals` | List of network hospital names |
| `submission_rules` | Deadline, minimum amount, currency |
| `document_requirements` | Required/optional documents per category |
| `fraud_thresholds` | Fraud detection trigger values |
| `members` | Complete member roster with join dates |

### `test_cases.json` — Test Scenarios

12 test cases (TC001-TC012) covering:
- TC001: Normal consultation claim
- TC002: Pharmacy claim
- TC003: Dental excluded procedure
- TC004: Vision claim
- TC005: Claim below minimum amount
- TC006: Waiting period violation
- TC007: Pre-authorization requirement
- TC008: High-value claim (fraud trigger)
- TC009: Network hospital discount
- TC010: Duplicate document types
- TC011: Simulated component failure (graceful degradation)
- TC012: Patient name mismatch across documents

---

## Data Flow Summary

```
User fills form → Frontend builds FormData
         ↓
Frontend POSTs to /api/v1/claims/upload
         ↓
Backend saves files to uploads/{claim_id}/
Backend creates claim record (status: pending)
Backend inserts document records
         ↓
Pipeline starts (status: processing)
         ↓
Agent 1: Are all required documents present? → Yes/No
Agent 2: What does each document say? → Extracted fields
Agent 3: Do documents agree? → Consistency check
Agent 4: Is this covered by policy? → Approved amount
Agent 5: Is this suspicious? → Fraud score
Agent 6: What's the verdict? → Decision + explanation
         ↓
Pipeline completes (status: completed)
         ↓
Frontend receives claim_id
Frontend fetches /claims/{id}/decision and /claims/{id}/trace
Frontend displays decision + trace
```
