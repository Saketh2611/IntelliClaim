import React, { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardList,
  FileText,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Trash2,
  UploadCloud,
  XCircle,
} from "lucide-react";

const API_BASE_DEFAULT =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api/v1";
const API_KEY_DEFAULT = import.meta.env.VITE_API_KEY || "dev-key";

const CATEGORIES = [
  "CONSULTATION",
  "PHARMACY",
  "DENTAL",
  "DIAGNOSTIC",
  "VISION",
  "ALTERNATIVE_MEDICINE",
];

const DOC_TYPES = [
  "PRESCRIPTION",
  "HOSPITAL_BILL",
  "PHARMACY_BILL",
  "LAB_REPORT",
  "DIAGNOSTIC_REPORT",
  "DISCHARGE_SUMMARY",
  "DENTAL_REPORT",
  "OTHER",
];

const DOC_REQUIREMENTS = {
  CONSULTATION: ["PRESCRIPTION", "HOSPITAL_BILL"],
  PHARMACY: ["PRESCRIPTION", "PHARMACY_BILL"],
  DENTAL: ["HOSPITAL_BILL"],
  DIAGNOSTIC: ["PRESCRIPTION", "LAB_REPORT", "HOSPITAL_BILL"],
  VISION: ["PRESCRIPTION", "HOSPITAL_BILL"],
  ALTERNATIVE_MEDICINE: ["PRESCRIPTION", "HOSPITAL_BILL"],
};

const STATUS_ICONS = {
  passed: CheckCircle2,
  failed: XCircle,
  degraded: AlertTriangle,
  skipped: RefreshCw,
};

function newDocument(type = "PRESCRIPTION") {
  const id =
    globalThis.crypto?.randomUUID?.() ||
    `doc_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  return { id, document_type: type, file: null };
}

function formatAmount(value) {
  const number = Number(value || 0);
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(number);
}

async function readJson(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function normalizeError(payload) {
  const detail = payload?.detail ?? payload;
  if (!detail) return "Request failed.";
  if (typeof detail === "string") return detail;

  if (detail.error === "DOCUMENT_VALIDATION") {
    const uploaded = detail.submitted_types?.join(", ") || "none";
    const missing = detail.missing_types?.join(", ") || "required document";
    return `Document validation failed. Uploaded: ${uploaded}. Missing: ${missing}.`;
  }

  if (detail.error === "UNREADABLE_DOCUMENT") {
    return `Unreadable document: ${detail.file_name}.`;
  }

  if (detail.error === "PATIENT_MISMATCH") {
    return `Patient mismatch detected: ${JSON.stringify(detail.mismatches)}`;
  }

  return JSON.stringify(detail, null, 2);
}

function App() {
  const [apiBase, setApiBase] = useState(API_BASE_DEFAULT);
  const [apiKey, setApiKey] = useState(API_KEY_DEFAULT);
  const [reviewClaimId, setReviewClaimId] = useState("");
  const [form, setForm] = useState({
    member_id: "EMP001",
    policy_id: "PLUM_GHI_2024",
    claim_category: "CONSULTATION",
    claimed_amount: "1500",
    treatment_date: "2024-11-01",
    hospital_name: "",
    simulate_component_failure: false,
  });
  const [documents, setDocuments] = useState([
    newDocument("PRESCRIPTION"),
    newDocument("HOSPITAL_BILL"),
  ]);
  const [submitting, setSubmitting] = useState(false);
  const [loadingClaim, setLoadingClaim] = useState(false);
  const [error, setError] = useState("");
  const [submission, setSubmission] = useState(null);
  const [claimRecord, setClaimRecord] = useState(null);
  const [decision, setDecision] = useState(null);
  const [trace, setTrace] = useState(null);

  const requiredDocs = DOC_REQUIREMENTS[form.claim_category] || [];

  const requestPreview = useMemo(
    () => ({
      member_id: form.member_id,
      policy_id: form.policy_id,
      claim_category: form.claim_category,
      claimed_amount: Number(form.claimed_amount || 0),
      treatment_date: form.treatment_date,
      hospital_name: form.hospital_name || null,
      simulate_component_failure: form.simulate_component_failure,
      documents: documents.map((doc) => ({
        document_type: doc.document_type,
        file_name: doc.file?.name || null,
        mime_type: doc.file?.type || null,
      })),
    }),
    [documents, form],
  );

  function updateForm(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function updateDocument(id, patch) {
    setDocuments((current) =>
      current.map((doc) => (doc.id === id ? { ...doc, ...patch } : doc)),
    );
  }

  function addDocument(type = requiredDocs[0] || "PRESCRIPTION") {
    setDocuments((current) => [...current, newDocument(type)]);
  }

  function loadRequiredSet() {
    const next = (requiredDocs.length ? requiredDocs : ["PRESCRIPTION"]).map(
      (type) => newDocument(type),
    );
    setDocuments(next);
  }

  async function fetchJson(path) {
    const response = await fetch(`${apiBase.replace(/\/$/, "")}${path}`, {
      headers: { "x-api-key": apiKey },
    });
    const data = await readJson(response);
    if (!response.ok) {
      throw data;
    }
    return data;
  }

  async function loadClaimArtifacts(claimId) {
    const [claimResult, decisionResult, traceResult] = await Promise.allSettled([
      fetchJson(`/claims/${claimId}`),
      fetchJson(`/claims/${claimId}/decision`),
      fetchJson(`/claims/${claimId}/trace`),
    ]);

    setClaimRecord(claimResult.status === "fulfilled" ? claimResult.value : null);
    setDecision(
      decisionResult.status === "fulfilled"
        ? decisionResult.value
        : claimResult.value?.decision || null,
    );
    setTrace(traceResult.status === "fulfilled" ? traceResult.value : null);

    const failed = [claimResult, decisionResult, traceResult].find(
      (item) => item.status === "rejected",
    );
    if (failed) {
      setError(normalizeError(failed.reason));
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setSubmission(null);
    setDecision(null);
    setTrace(null);
    setClaimRecord(null);

    const missingFiles = documents.filter((doc) => !doc.file);
    if (!documents.length || missingFiles.length) {
      setError("Each document row needs a file.");
      return;
    }

    const body = new FormData();
    body.append("member_id", form.member_id.trim());
    body.append("policy_id", form.policy_id.trim());
    body.append("claim_category", form.claim_category);
    body.append("claimed_amount", form.claimed_amount);
    body.append("treatment_date", form.treatment_date);
    body.append("hospital_name", form.hospital_name.trim());
    body.append("simulate_component_failure", String(form.simulate_component_failure));
    body.append(
      "document_types",
      JSON.stringify(documents.map((doc) => doc.document_type)),
    );
    documents.forEach((doc) => body.append("files", doc.file));

    setSubmitting(true);
    try {
      const response = await fetch(`${apiBase.replace(/\/$/, "")}/claims/upload`, {
        method: "POST",
        headers: { "x-api-key": apiKey },
        body,
      });
      const data = await readJson(response);
      if (!response.ok) {
        throw data;
      }
      setSubmission(data);
      setReviewClaimId(data.claim_id);
      await loadClaimArtifacts(data.claim_id);
    } catch (err) {
      setError(normalizeError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleReview(event) {
    event.preventDefault();
    if (!reviewClaimId.trim()) return;
    setError("");
    setLoadingClaim(true);
    try {
      await loadClaimArtifacts(reviewClaimId.trim());
    } catch (err) {
      setError(normalizeError(err));
    } finally {
      setLoadingClaim(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Plum Claims Operations</p>
          <h1>IntelliClaim</h1>
        </div>
        <form className="review-form" onSubmit={handleReview}>
          <label>
            <span>Claim ID</span>
            <input
              value={reviewClaimId}
              onChange={(event) => setReviewClaimId(event.target.value)}
              placeholder="UUID"
            />
          </label>
          <button type="submit" disabled={loadingClaim}>
            {loadingClaim ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
            Review
          </button>
        </form>
      </header>

      <section className="connection-strip">
        <label>
          <span>Backend URL</span>
          <input value={apiBase} onChange={(event) => setApiBase(event.target.value)} />
        </label>
        <label>
          <span>API Key</span>
          <input
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            type="password"
          />
        </label>
      </section>

      {error && (
        <section className="banner error-banner">
          <AlertTriangle size={18} />
          <pre>{error}</pre>
        </section>
      )}

      <div className="workspace">
        <form className="panel claim-form" onSubmit={handleSubmit}>
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Submission</p>
              <h2>Claim Details</h2>
            </div>
            <button type="button" className="secondary" onClick={loadRequiredSet}>
              <RefreshCw size={16} />
              Required Set
            </button>
          </div>

          <div className="field-grid">
            <label>
              <span>Member ID</span>
              <input
                value={form.member_id}
                onChange={(event) => updateForm("member_id", event.target.value)}
                required
              />
            </label>
            <label>
              <span>Policy ID</span>
              <input
                value={form.policy_id}
                onChange={(event) => updateForm("policy_id", event.target.value)}
                required
              />
            </label>
            <label>
              <span>Category</span>
              <select
                value={form.claim_category}
                onChange={(event) => updateForm("claim_category", event.target.value)}
              >
                {CATEGORIES.map((category) => (
                  <option key={category} value={category}>
                    {category}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Claimed Amount</span>
              <input
                type="number"
                min="1"
                step="0.01"
                value={form.claimed_amount}
                onChange={(event) => updateForm("claimed_amount", event.target.value)}
                required
              />
            </label>
            <label>
              <span>Treatment Date</span>
              <input
                type="date"
                value={form.treatment_date}
                onChange={(event) => updateForm("treatment_date", event.target.value)}
                required
              />
            </label>
            <label>
              <span>Hospital</span>
              <input
                value={form.hospital_name}
                onChange={(event) => updateForm("hospital_name", event.target.value)}
                placeholder="Apollo Hospitals"
              />
            </label>
          </div>

          <label className="toggle-row">
            <input
              type="checkbox"
              checked={form.simulate_component_failure}
              onChange={(event) =>
                updateForm("simulate_component_failure", event.target.checked)
              }
            />
            <span>Simulate component failure</span>
          </label>

          <section className="required-band">
            <span>Required</span>
            <div>
              {requiredDocs.map((type) => (
                <strong key={type}>{type}</strong>
              ))}
            </div>
          </section>

          <section className="documents-block">
            <div className="section-title">
              <FileText size={17} />
              <h3>Documents</h3>
            </div>
            <div className="document-list">
              {documents.map((doc, index) => (
                <div className="document-row" key={doc.id}>
                  <span className="row-index">{index + 1}</span>
                  <select
                    value={doc.document_type}
                    onChange={(event) =>
                      updateDocument(doc.id, { document_type: event.target.value })
                    }
                  >
                    {DOC_TYPES.map((type) => (
                      <option key={type} value={type}>
                        {type}
                      </option>
                    ))}
                  </select>
                  <label className="file-picker">
                    <UploadCloud size={16} />
                    <span>{doc.file?.name || "Choose file"}</span>
                    <input
                      type="file"
                      accept="image/*,.pdf"
                      onChange={(event) =>
                        updateDocument(doc.id, { file: event.target.files?.[0] || null })
                      }
                    />
                  </label>
                  <button
                    type="button"
                    className="icon-button"
                    onClick={() =>
                      setDocuments((current) =>
                        current.filter((item) => item.id !== doc.id),
                      )
                    }
                    title="Remove document"
                    aria-label="Remove document"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
            </div>
            <button type="button" className="secondary add-button" onClick={() => addDocument()}>
              <Plus size={16} />
              Add Document
            </button>
          </section>

          <div className="submit-row">
            <button type="submit" disabled={submitting}>
              {submitting ? <Loader2 className="spin" size={16} /> : <Send size={16} />}
              Submit Claim
            </button>
            <span>{formatAmount(form.claimed_amount)}</span>
          </div>
        </form>

        <aside className="side-stack">
          <section className="panel result-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Decision</p>
                <h2>{decision?.outcome || "Pending"}</h2>
              </div>
              <OutcomeBadge outcome={decision?.outcome} />
            </div>
            <DecisionSummary decision={decision} submission={submission} claimRecord={claimRecord} />
          </section>

          <section className="panel preview-panel">
            <div className="section-title">
              <ClipboardList size={17} />
              <h3>Request Preview</h3>
            </div>
            <pre>{JSON.stringify(requestPreview, null, 2)}</pre>
          </section>
        </aside>
      </div>

      <section className="panel trace-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Explainability</p>
            <h2>Trace</h2>
          </div>
          {trace && (
            <div className="trace-counts">
              <span>{trace.total_steps} steps</span>
              <strong>{trace.failed_steps} flagged</strong>
            </div>
          )}
        </div>
        <TraceTimeline trace={trace} />
      </section>
    </main>
  );
}

function DecisionSummary({ decision, submission, claimRecord }) {
  if (!decision) {
    return (
      <div className="empty-state">
        <ShieldCheck size={22} />
        <span>No decision loaded</span>
      </div>
    );
  }

  const breakdown = decision.breakdown || claimRecord?.decision?.breakdown || [];

  return (
    <div className="decision-body">
      {submission?.claim_id && (
        <div className="claim-id">
          <span>Claim</span>
          <code>{submission.claim_id}</code>
        </div>
      )}
      <dl className="metric-grid">
        <div>
          <dt>Approved</dt>
          <dd>{formatAmount(decision.approved_amount)}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{Math.round(Number(decision.confidence || 0) * 100)}%</dd>
        </div>
      </dl>
      <p className="reason">{decision.reason}</p>

      {!!breakdown.length && (
        <div className="line-items">
          <h3>Line Items</h3>
          {breakdown.map((item, index) => (
            <div className="line-item" key={`${item.description}_${index}`}>
              <div>
                <strong>{item.description}</strong>
                <span>{item.reason}</span>
              </div>
              <div>
                <b>{formatAmount(item.amount)}</b>
                <OutcomeBadge outcome={item.status} compact />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function OutcomeBadge({ outcome, compact = false }) {
  const label = outcome || "WAITING";
  return <span className={`outcome-badge ${label.toLowerCase()} ${compact ? "compact" : ""}`}>{label}</span>;
}

function TraceTimeline({ trace }) {
  if (!trace?.steps?.length) {
    return (
      <div className="empty-state trace-empty">
        <ClipboardList size={22} />
        <span>No trace loaded</span>
      </div>
    );
  }

  return (
    <div className="trace-list">
      {trace.steps.map((step, index) => {
        const Icon = STATUS_ICONS[step.status] || RefreshCw;
        return (
          <article className={`trace-step ${step.status}`} key={`${step.step_name}_${index}`}>
            <div className="trace-marker">
              <Icon size={17} />
            </div>
            <div className="trace-main">
              <div className="trace-head">
                <div>
                  <h3>{step.step_name}</h3>
                  <span>{step.created_at}</span>
                </div>
                <div>
                  <OutcomeBadge outcome={step.status} compact />
                  {step.duration_ms != null && <code>{step.duration_ms} ms</code>}
                </div>
              </div>
              {step.error_message && <p className="trace-error">{step.error_message}</p>}
              <div className="trace-details">
                <details>
                  <summary>Input</summary>
                  <pre>{JSON.stringify(step.input_snapshot || {}, null, 2)}</pre>
                </details>
                <details>
                  <summary>Output</summary>
                  <pre>{JSON.stringify(step.output_snapshot || {}, null, 2)}</pre>
                </details>
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}

export default App;
