# Testing Guide

## Quick Start

```bash
# Start the app
docker-compose up --build

# Open browser
http://localhost
```

**API Key for all requests:** `will be shared in mail` (header: `X-API-Key`)

---

## Test Members

| Member ID | Name | Join Date | Notes |
|-----------|------|-----------|-------|
| EMP001 | Rajesh Kumar | 2024-04-01 | Standard member, long tenure |
| EMP002 | Priya Singh | 2024-04-01 | Use for dental tests |
| EMP003 | Amit Verma | 2024-04-01 | Use for per-claim limit tests |
| EMP004 | Sneha Reddy | 2024-04-01 | Use for pharmacy tests |
| EMP005 | Vikram Joshi | 2024-09-01 | **Late join** — use for waiting period tests |
| EMP006 | Kavita Nair | 2024-04-01 | Use for component failure tests |
| EMP007 | Suresh Patil | 2024-04-01 | Use for MRI/diagnostic tests |
| EMP008 | Ravi Menon | 2024-04-01 | Use for fraud signal tests |
| EMP009 | Anita Desai | 2024-04-01 | Use for excluded treatment tests |
| EMP010 | Deepak Shah | 2024-04-01 | Use for network hospital tests |

---

## Test Cases (from test_cases.json)

### TC001 — Wrong Document Uploaded

**What to test:** System stops when required documents are missing/duplicated.

| Field | Value |
|-------|-------|
| Member ID | EMP001 |
| Category | CONSULTATION |
| Amount | 1500 |
| Treatment Date | 2024-11-01 |
| Documents | Upload 2x PRESCRIPTION (no hospital bill) |

**Expected:** Error — tells you specifically that HOSPITAL_BILL is missing.

---

### TC002 — Unreadable Document

**What to test:** System detects unreadable document and asks for re-upload.

| Field | Value |
|-------|-------|
| Member ID | EMP004 |
| Category | PHARMACY |
| Amount | 800 |
| Treatment Date | 2024-10-25 |
| Documents | 1 clear PRESCRIPTION + 1 blurry/corrupted PHARMACY_BILL |

**Expected:** Error — identifies that the pharmacy bill can't be read, asks to re-upload.

---

### TC003 — Patient Name Mismatch

**What to test:** System catches documents belonging to different patients.

| Field | Value |
|-------|-------|
| Member ID | EMP001 |
| Category | CONSULTATION |
| Amount | 1500 |
| Treatment Date | 2024-11-01 |
| Documents | PRESCRIPTION (patient: "Rajesh Kumar") + HOSPITAL_BILL (patient: "Arjun Mehta") |

**Expected:** Error — names the mismatched patients from each document.

---

### TC004 — Clean Consultation (Full Approval)

**What to test:** Happy path with co-pay deduction.

| Field | Value |
|-------|-------|
| Member ID | EMP001 |
| Category | CONSULTATION |
| Amount | 1500 |
| Treatment Date | 2024-11-01 |
| Hospital Name | _(leave empty)_ |
| Documents | PRESCRIPTION + HOSPITAL_BILL (use sample PDFs) |

**Expected:** APPROVED — ₹1,350 (10% co-pay of ₹150 deducted).

---

### TC005 — Waiting Period (Diabetes)

**What to test:** Condition-specific waiting period rejection.

| Field | Value |
|-------|-------|
| Member ID | EMP005 |
| Category | CONSULTATION |
| Amount | 3000 |
| Treatment Date | 2024-10-15 |
| Documents | PRESCRIPTION (diagnosis: "Type 2 Diabetes") + HOSPITAL_BILL |

**Expected:** REJECTED — waiting period for diabetes is 90 days, member joined 2024-09-01 (only 44 days elapsed). Message states eligible date.

---

### TC006 — Dental Partial Approval

**What to test:** Line-item level approval/rejection.

| Field | Value |
|-------|-------|
| Member ID | EMP002 |
| Category | DENTAL |
| Amount | 12000 |
| Treatment Date | 2024-10-15 |
| Documents | HOSPITAL_BILL with items: "Root Canal Treatment ₹8000" + "Teeth Whitening ₹4000" |

**Expected:** PARTIAL — ₹8,000 approved. Teeth whitening rejected (cosmetic exclusion). Line items show individual reasons.

---

### TC007 — MRI Without Pre-Authorization

**What to test:** Pre-auth requirement enforcement.

| Field | Value |
|-------|-------|
| Member ID | EMP007 |
| Category | DIAGNOSTIC |
| Amount | 15000 |
| Treatment Date | 2024-11-02 |
| Documents | PRESCRIPTION + LAB_REPORT + HOSPITAL_BILL (all mentioning "MRI Lumbar Spine") |

**Expected:** REJECTED — PRE_AUTH_MISSING. Explains pre-authorization was needed and how to resubmit.

---

### TC008 — Per-Claim Limit Exceeded

**What to test:** Per-claim limit of ₹5,000.

| Field | Value |
|-------|-------|
| Member ID | EMP003 |
| Category | CONSULTATION |
| Amount | 7500 |
| Treatment Date | 2024-10-20 |
| Documents | PRESCRIPTION + HOSPITAL_BILL |

**Expected:** REJECTED — PER_CLAIM_EXCEEDED. States the ₹5,000 limit and ₹7,500 claimed.

---

### TC009 — Fraud Signal (Same-Day Claims)

**What to test:** Fraud detection routes to manual review.

| Field | Value |
|-------|-------|
| Member ID | EMP008 |
| Category | CONSULTATION |
| Amount | 4800 |
| Treatment Date | 2024-10-30 |
| Documents | PRESCRIPTION + HOSPITAL_BILL |

**Expected:** MANUAL_REVIEW — flags same-day claim pattern. Requires claim history in DB (submit 3+ claims for EMP008 on 2024-10-30 first).

---

### TC010 — Network Hospital Discount

**What to test:** Discount applied BEFORE co-pay.

| Field | Value |
|-------|-------|
| Member ID | EMP010 |
| Category | CONSULTATION |
| Amount | 4500 |
| Treatment Date | 2024-11-03 |
| Hospital Name | **Apollo Hospitals** |
| Documents | PRESCRIPTION + HOSPITAL_BILL (hospital: "Apollo Hospitals") |

**Expected:** APPROVED — ₹3,240. Calculation: ₹4,500 → 20% network discount = ₹3,600 → 10% co-pay = ₹3,240.

---

### TC011 — Component Failure (Graceful Degradation)

**What to test:** Pipeline continues when a component fails.

| Field | Value |
|-------|-------|
| Member ID | EMP006 |
| Category | ALTERNATIVE_MEDICINE |
| Amount | 4000 |
| Treatment Date | 2024-10-28 |
| Simulate Failure | **true** |
| Documents | PRESCRIPTION + HOSPITAL_BILL |

**Expected:** MANUAL_REVIEW (or APPROVED with reduced confidence). Must NOT crash. Trace shows which component failed. Confidence < 1.0.

---

### TC012 — Excluded Treatment

**What to test:** Policy exclusion enforcement.

| Field | Value |
|-------|-------|
| Member ID | EMP009 |
| Category | CONSULTATION |
| Amount | 8000 |
| Treatment Date | 2024-10-18 |
| Documents | PRESCRIPTION (diagnosis: "Morbid Obesity", treatment: "Bariatric Consultation") + HOSPITAL_BILL |

**Expected:** REJECTED — EXCLUDED_CONDITION. Obesity treatment is explicitly excluded.

---

## Testing via cURL

### Basic claim submission with file upload:

```bash
curl -X POST http://localhost:8000/api/v1/claims/upload \
  -H "X-API-Key: sk-intelliclaim-sak-26112003" \
  -F "member_id=EMP001" \
  -F "claim_category=CONSULTATION" \
  -F "claimed_amount=1500" \
  -F "treatment_date=2024-11-01" \
  -F 'document_types=["PRESCRIPTION", "HOSPITAL_BILL"]' \
  -F "files=@prescription_rajesh_kumar.pdf" \
  -F "files=@hospital_bill_rajesh_kumar.pdf"
```

### With network hospital:

```bash
curl -X POST http://localhost:8000/api/v1/claims/upload \
  -H "X-API-Key: sk-intelliclaim-sak-26112003" \
  -F "member_id=EMP010" \
  -F "claim_category=CONSULTATION" \
  -F "claimed_amount=4500" \
  -F "treatment_date=2024-11-03" \
  -F "hospital_name=Apollo Hospitals" \
  -F 'document_types=["PRESCRIPTION", "HOSPITAL_BILL"]' \
  -F "files=@prescription.pdf" \
  -F "files=@hospital_bill.pdf"
```

### Check a claim result:

```bash
curl http://localhost:8000/api/v1/claims/{claim_id} \
  -H "X-API-Key: sk-intelliclaim-sak-26112003"
```

### View execution trace:

```bash
curl http://localhost:8000/api/v1/claims/{claim_id}/trace \
  -H "X-API-Key: sk-intelliclaim-sak-26112003"
```

---

## Cleaning Test Data

If fraud detection triggers due to accumulated test claims, clean a member's history:

```sql
-- Run in Supabase SQL editor
DELETE FROM decisions WHERE claim_id IN (SELECT claim_id FROM claims WHERE member_id = 'EMP001');
DELETE FROM trace_steps WHERE claim_id IN (SELECT claim_id FROM claims WHERE member_id = 'EMP001');
DELETE FROM documents WHERE claim_id IN (SELECT claim_id FROM claims WHERE member_id = 'EMP001');
DELETE FROM claims WHERE member_id = 'EMP001';
```

Or clean everything:

```sql
DELETE FROM decisions;
DELETE FROM trace_steps;
DELETE FROM documents;
DELETE FROM claims;
```

---

## Tips

- **Fresh member for clean tests:** Use a member with no claim history to avoid fraud flags.
- **Fraud test setup:** Submit 3+ claims for EMP008 on the same date first, then submit TC009 to trigger the fraud signal.
- **Sample PDFs:** Place test documents in `sample_documents/` — use the prescription and hospital bill PDFs provided.
- **Per-claim limit:** Applies to all categories — ₹5,000 max per claim.
- **Waiting periods:** Only EMP005 (joined 2024-09-01) is useful for waiting period tests. All other members joined 2024-04-01 and have passed all waiting periods for treatment dates in Oct/Nov 2024.
