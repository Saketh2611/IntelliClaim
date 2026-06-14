-- ============================================
-- ENUMS
-- ============================================

CREATE TYPE claim_status AS ENUM (
  'pending',
  'processing',
  'completed',
  'failed'
);

CREATE TYPE claim_category AS ENUM (
  'CONSULTATION',
  'PHARMACY',
  'DENTAL',
  'DIAGNOSTIC',
  'VISION',
  'ALTERNATIVE_MEDICINE'
);

CREATE TYPE document_type AS ENUM (
  'PRESCRIPTION',
  'HOSPITAL_BILL',
  'PHARMACY_BILL',
  'LAB_REPORT',
  'DIAGNOSTIC_REPORT',
  'DISCHARGE_SUMMARY',
  'DENTAL_REPORT',
  'OTHER'
);

CREATE TYPE decision_outcome AS ENUM (
  'APPROVED',
  'PARTIAL',
  'REJECTED',
  'MANUAL_REVIEW'
);

CREATE TYPE trace_status AS ENUM (
  'passed',
  'failed',
  'skipped',
  'degraded'
);

CREATE TYPE member_relationship AS ENUM (
  'SELF',
  'SPOUSE',
  'CHILD',
  'PARENT'
);

-- ============================================
-- MEMBERS
-- seeded from policy_terms.json on startup
-- ============================================

CREATE TABLE members (
  member_id          TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  date_of_birth      DATE NOT NULL,
  gender             TEXT NOT NULL,
  relationship       member_relationship NOT NULL,
  join_date          DATE NOT NULL,               -- waiting period calculations
  primary_member_id  TEXT REFERENCES members(member_id),  -- null for SELF
  policy_id          TEXT NOT NULL,
  dependents         JSONB DEFAULT '[]',           -- array of dependent member_ids
  created_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_members_primary ON members(primary_member_id);
CREATE INDEX idx_members_policy  ON members(policy_id);

-- ============================================
-- CLAIMS
-- ============================================

CREATE TABLE claims (
  claim_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  member_id       TEXT NOT NULL REFERENCES members(member_id) ON DELETE RESTRICT,
  policy_id       TEXT NOT NULL,
  claim_category  claim_category NOT NULL,
  claimed_amount  NUMERIC(12, 2) NOT NULL,
  treatment_date  DATE NOT NULL,                  -- submission deadline + waiting period math
  hospital_name   TEXT,                           -- network hospital detection
  status          claim_status NOT NULL DEFAULT 'pending',
  submitted_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_claims_member_id      ON claims(member_id);
CREATE INDEX idx_claims_status         ON claims(status);
CREATE INDEX idx_claims_treatment_date ON claims(treatment_date);
CREATE INDEX idx_claims_submitted      ON claims(submitted_at DESC);
CREATE INDEX idx_claims_member_date    ON claims(member_id, treatment_date); -- fraud: same-day claims

-- ============================================
-- DOCUMENTS
-- ============================================

CREATE TABLE documents (
  document_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id        UUID NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
  document_type   document_type NOT NULL,
  file_path       TEXT NOT NULL,
  mime_type       TEXT NOT NULL,
  extracted_data  JSONB DEFAULT NULL,       -- LLM structured extraction output
  is_readable     BOOLEAN DEFAULT NULL,     -- null=not attempted, true/false after extraction
  uploaded_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_documents_claim_id ON documents(claim_id);

-- ============================================
-- DECISIONS
-- 1:1 with claims
-- ============================================

CREATE TABLE decisions (
  decision_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id        UUID NOT NULL UNIQUE REFERENCES claims(claim_id) ON DELETE CASCADE,
  outcome         decision_outcome NOT NULL,
  approved_amount NUMERIC(12, 2) DEFAULT 0,
  reason          TEXT NOT NULL,
  breakdown       JSONB DEFAULT NULL,       -- line-item breakdown for PARTIAL decisions
  confidence      FLOAT NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  decided_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- breakdown structure example (TC006 dental partial):
-- {
--   "line_items": [
--     { "description": "Root Canal Treatment", "amount": 8000, "status": "APPROVED", "reason": "Covered procedure" },
--     { "description": "Teeth Whitening",       "amount": 4000, "status": "REJECTED", "reason": "Cosmetic exclusion" }
--   ],
--   "network_discount_applied": 0,
--   "copay_deducted": 0
-- }

CREATE INDEX idx_decisions_outcome  ON decisions(outcome);
CREATE INDEX idx_decisions_claim_id ON decisions(claim_id);

-- ============================================
-- TRACE STEPS
-- one row per processing stage per claim
-- ============================================

CREATE TABLE trace_steps (
  trace_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id        UUID NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
  step_name       TEXT NOT NULL,
  status          trace_status NOT NULL,
  input_snapshot  JSONB DEFAULT NULL,
  output_snapshot JSONB DEFAULT NULL,
  error_message   TEXT DEFAULT NULL,
  duration_ms     INTEGER DEFAULT NULL,
  created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_trace_claim_id  ON trace_steps(claim_id);
CREATE INDEX idx_trace_step_name ON trace_steps(step_name);
CREATE INDEX idx_trace_created   ON trace_steps(created_at ASC);

-- ============================================
-- ROW LEVEL SECURITY
-- ============================================

ALTER TABLE members     ENABLE ROW LEVEL SECURITY;
ALTER TABLE claims      ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents   ENABLE ROW LEVEL SECURITY;
ALTER TABLE decisions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE trace_steps ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service role full access" ON members     FOR ALL USING (true);
CREATE POLICY "service role full access" ON claims      FOR ALL USING (true);
CREATE POLICY "service role full access" ON documents   FOR ALL USING (true);
CREATE POLICY "service role full access" ON decisions   FOR ALL USING (true);
CREATE POLICY "service role full access" ON trace_steps FOR ALL USING (true);