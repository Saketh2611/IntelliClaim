# IntelliClaim User Guide

## What is IntelliClaim?

IntelliClaim is an AI-powered health insurance claims processing system built for Plum. It automates the entire workflow of submitting, validating, and deciding on OPD (Out-Patient Department) health insurance claims. Instead of waiting days for manual review, claims are processed in seconds through a multi-agent AI pipeline that validates documents, checks policy compliance, detects fraud, and delivers an explainable decision.

---

## Getting Started

### Prerequisites

- Docker and Docker Compose installed
- A Supabase account with a configured database
- A Groq API key (free at console.groq.com)
- Node.js 18+ (for local frontend development)
- Python 3.12+ (for local backend development)

### Environment Setup

Create a `.env` file in the project root:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
GROQ_API_KEY=your-groq-api-key
AI_API_KEY=your-custom-api-key-for-auth
```

### Running with Docker (Recommended)

```bash
docker-compose up --build
```

This starts three services:
- **Nginx** on port 80 (reverse proxy)
- **Frontend** (React app)
- **Backend** (FastAPI server)

Open your browser at `http://localhost` to access the application.

### Running Locally (Development)

**Backend:**
```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

### Database Setup

Run the seed script to populate the database with test members:
```bash
python -m db.seed
```

---

## Using the Web Interface

### The Claims Form (Left Panel)

The web interface has a clean form on the left side for submitting claims:

#### Step 1: Enter Member Details

- **Member ID** — Enter your employee ID (e.g., `EMP001`) or dependent ID (e.g., `DEP001`)
- **Policy ID** — Pre-filled as `PLUM_GHI_2024` (the active group policy)

#### Step 2: Select Claim Category

Choose the type of treatment:

| Category | For | Sub-Limit |
|----------|-----|-----------|
| Consultation | Doctor visits, follow-ups | INR 2,000 |
| Pharmacy | Medicines, prescriptions | INR 15,000 |
| Dental | Dental procedures | INR 10,000 |
| Diagnostic | Lab tests, scans, imaging | INR 10,000 |
| Vision | Glasses, eye exams, eye surgery | INR 5,000 |
| Alternative Medicine | Ayurveda, Homeopathy, Naturopathy | INR 8,000 |

#### Step 3: Fill Claim Details

- **Claimed Amount** — Total amount in INR (minimum INR 500)
- **Treatment Date** — When the treatment occurred (must be within last 30 days)
- **Hospital Name** — Optional, but enter it for network discount eligibility

#### Step 4: Upload Documents

The form dynamically shows which documents are required based on your selected category:

**Required documents must be uploaded.** The system will reject claims with missing required documents.

Supported file formats:
- PDF documents
- Images (JPEG, PNG)

Drag and drop files or click to browse. For each file, select its document type from the dropdown.

#### Step 5: Submit

Click **Submit Claim**. The AI pipeline processes your claim in real-time (typically 5-15 seconds).

---

### Understanding the Decision (Right Panel)

After submission, the decision panel shows:

#### Decision Badge
- **APPROVED** (green) — Full claim amount approved
- **PARTIAL** (yellow) — Some amount approved, some rejected
- **REJECTED** (red) — Claim not eligible under policy
- **MANUAL REVIEW** (blue) — Sent for human adjudicator review

#### Decision Details
- **Approved Amount** — How much will be reimbursed
- **Confidence Score** — How certain the AI is (0-100%)
- **Reason** — Plain-English explanation of the decision
- **Line Items** — Breakdown showing each item's approval/rejection reason

#### Applied Adjustments
- **Network Discount** — Discount applied for using network hospitals
- **Co-pay Deducted** — Your share of the cost (varies by category)

---

### The Trace Timeline (Bottom Panel)

Every claim shows a full execution trace — a step-by-step log of how the AI processed your claim:

| Step | What It Does |
|------|-------------|
| Document Validator | Checks you uploaded all required documents |
| Extractor | AI reads and extracts information from your documents |
| Cross-Doc Validator | Verifies patient name and details match across all documents |
| Policy Checker | Validates claim against policy rules, limits, and exclusions |
| Fraud Detector | Scores claim for potential fraud indicators |
| Decision Maker | Makes the final decision based on all previous steps |

Each step shows:
- **Status icon** — Green check (passed), red X (failed), yellow warning (degraded)
- **Duration** — How long the step took
- **Input/Output** — Expandable sections showing what data went in and came out

---

## Common Scenarios

### Scenario 1: Simple Consultation Claim

1. Enter Member ID: `EMP001`
2. Category: `CONSULTATION`
3. Amount: `1500`
4. Date: Today's date
5. Hospital: `Apollo Hospitals` (for network discount)
6. Upload: Prescription PDF + Hospital Bill PDF
7. Submit

**Expected:** APPROVED with 20% network discount applied, 10% co-pay deducted.

### Scenario 2: Claim Exceeding Sub-Limit

1. Category: `CONSULTATION`
2. Amount: `3000` (exceeds INR 2,000 sub-limit)
3. Upload required documents

**Expected:** PARTIAL approval for INR 2,000 (capped at sub-limit).

### Scenario 3: Missing Documents

1. Category: `CONSULTATION`
2. Upload only a hospital bill (missing prescription)

**Expected:** Immediate rejection with error: "Missing required documents: PRESCRIPTION"

### Scenario 4: Excluded Procedure

1. Category: `DENTAL`
2. Documents mention "Teeth Whitening"

**Expected:** REJECTED — teeth whitening is an excluded procedure.

### Scenario 5: Waiting Period Not Met

1. Member: `EMP005` (joined 2024-09-01, only 5 months of coverage)
2. Treatment for a condition with 365-day waiting period

**Expected:** REJECTED — specific condition waiting period not met.

---

## Document Types Explained

| Document Type | What It Is | When Required |
|---------------|-----------|---------------|
| PRESCRIPTION | Doctor's prescription with diagnosis and medications | Most categories |
| HOSPITAL_BILL | Itemized bill from hospital/clinic | Most categories |
| LAB_REPORT | Laboratory test results | Diagnostic claims |
| DIAGNOSTIC_REPORT | Imaging/scan reports (X-ray, MRI, etc.) | Optional for diagnostics |
| PHARMACY_BILL | Pharmacy receipt for medicines | Pharmacy claims |
| DENTAL_REPORT | Dental examination/procedure report | Optional for dental |
| DISCHARGE_SUMMARY | Hospital discharge summary | Optional for diagnostics |

---

## Policy Rules You Should Know

### Waiting Periods

| Condition | Waiting Period |
|-----------|---------------|
| All claims (new members) | 30 days from join date |
| Diabetes, Hypertension, Thyroid | 90 days |
| Mental Health | 180 days |
| Maternity | 270 days |
| Hernia, Cataract, Obesity | 365 days |
| Joint Replacement | 730 days (2 years) |

### What's Not Covered (Exclusions)

- Self-inflicted injuries
- Substance abuse treatment
- Experimental treatments
- Infertility treatments
- Cosmetic/aesthetic procedures
- Weight loss programs and bariatric surgery
- Teeth whitening, orthodontics (braces), dental implants
- LASIK, refractive surgery
- Non-medically necessary vaccinations
- Health supplements and tonics

### Network Hospitals (Get Discounts)

Visit these hospitals for automatic discounts:
- Apollo Hospitals (20% off consultations)
- Fortis Healthcare
- Max Healthcare
- Manipal Hospitals
- Narayana Health
- Medanta
- Kokilaben Dhirubhai Ambani Hospital
- Aster CMI Hospital
- Columbia Asia
- Sakra World Hospital

### Fraud Triggers

Your claim may be flagged for manual review if:
- You submit more than 2 claims on the same day
- You submit more than 6 claims in a month
- Claim amount exceeds INR 25,000
- High-value claim at a non-network hospital
- Multiple claims with the exact same amount

---

## Troubleshooting

### "Invalid API key" Error

The frontend needs the correct `AI_API_KEY` configured. Check your `.env` file.

### "Member not found"

Ensure the member ID exists in the database. Valid test IDs are EMP001-EMP010 and DEP001-DEP006.

### "Missing required documents"

Check the document requirements table for your claim category. Every required document type must be uploaded.

### "Unreadable document"

The AI couldn't extract information from your file. Common causes:
- Blurry or low-resolution images
- Heavily formatted or handwritten documents
- Password-protected PDFs
- Corrupted files

Try uploading a clearer version of the document.

### "Patient mismatch"

The patient names on different documents don't match. Ensure all documents (prescription, bill, lab report) show the same patient name.

### Claim shows "MANUAL_REVIEW"

This happens when:
- The pipeline confidence dropped below 70%
- A fraud indicator was detected
- A non-critical processing component failed

The claim needs review by a human adjudicator before a final decision.

### Slow Processing

AI document extraction typically takes 3-10 seconds per document. Claims with many documents may take up to 30 seconds. The Nginx timeout is set to 120 seconds.

---

## Architecture for Technical Users

```
┌─────────────────────────────────────────────────┐
│                    Nginx (port 80)                │
│    / → Frontend    /api/ → Backend               │
└────────────┬───────────────────┬────────────────┘
             │                   │
    ┌────────▼────────┐  ┌──────▼──────────────┐
    │  React Frontend │  │  FastAPI Backend     │
    │  (Vite + React) │  │  (Python 3.12)      │
    └─────────────────┘  └──────┬──────────────┘
                                │
                    ┌───────────▼───────────────┐
                    │    6-Agent Pipeline       │
                    │                           │
                    │  1. Document Validator    │
                    │  2. Extractor (OCR+Groq)  │
                    │  3. Cross-Doc Validator   │
                    │  4. Policy Checker        │
                    │  5. Fraud Detector        │
                    │  6. Decision Maker        │
                    └───────────┬───────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                  │
     ┌────────▼──────┐  ┌──────▼─────┐  ┌────────▼────────┐
     │   Supabase    │  │  Groq AI   │  │  File Storage   │
     │  (PostgreSQL) │  │(Llama 3.3) │  │  (uploads/)     │
     └───────────────┘  └────────────┘  └─────────────────┘
```

### Key Design Principles

1. **Deterministic First** — Critical decisions (approve/reject) are made by code logic, not AI. AI is only used for document reading and explanation generation.

2. **Graceful Degradation** — If the fraud detector or policy checker fails, the pipeline continues with reduced confidence rather than crashing.

3. **Full Traceability** — Every step is logged with inputs, outputs, timing, and errors. No black-box decisions.

4. **Policy-Driven** — All rules (limits, exclusions, waiting periods) are loaded from `policy_terms.json`, not hardcoded.
