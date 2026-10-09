import { useEffect, useRef, useState } from "react";

type HealthState = "checking" | "online" | "unavailable";
type DemoCase = { case_id: string; title: string; outcome: string; scenario: string };
type Evidence = { commit: string; path: string; start_line: number; end_line: number; excerpt: string; reference: string };
type Claim = { text: string; confidence?: string; evidence: Evidence[] };
type TraceEvent = { sequence: number; kind: string; stage?: string; tool?: string; status?: string; outcome?: string; stop_reason?: string };
type Replay = {
  case: DemoCase;
  run: {
    outcome: { status: string; stop_reason: string };
    events: TraceEvent[];
    report: { facts: Claim[]; root_cause_hypotheses: Claim[]; unresolved_questions: string[]; confidence: string };
  };
  patch_validation: { attempts: { candidate_origin: string; diff?: string; target_passed: boolean; regression_passed: boolean }[] } | null;
};
const outcomeMessages: Record<string, string> = {
  supported: "Evidence supports a root cause hypothesis.",
  insufficient_evidence: "The available evidence does not support a root cause.",
  budget_exhausted: "The investigation stopped at its resource limit.",
  unsafe_request: "The investigation stopped after an unsafe request.",
};

export function App() {
  const [health, setHealth] = useState<HealthState>("checking");
  const [cases, setCases] = useState<DemoCase[]>([]);
  const [listError, setListError] = useState(false);
  const [replay, setReplay] = useState<Replay | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [timeline, setTimeline] = useState<TraceEvent[]>([]);
  const [caseError, setCaseError] = useState<string | null>(null);
  const [traceError, setTraceError] = useState(false);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const streamRef = useRef<EventSource | null>(null);
  const requestId = useRef(0);

  async function pollTimeline(caseId: string, after: number, currentRequest = requestId.current) {
    try {
      const response = await fetch(`/api/demo/cases/${caseId}/progress/${after}`);
      if (!response.ok) throw new Error("Trace replay is unavailable");
      const body = await response.json() as { events?: TraceEvent[] };
      if (!Array.isArray(body.events)) throw new Error("Invalid trace replay");
      if (currentRequest !== requestId.current) return;
      setTimeline((current) => [...current, ...body.events!]);
      setTraceError(false);
    } catch {
      if (currentRequest === requestId.current) setTraceError(true);
    }
  }

  function replayTimeline(caseId: string, currentRequest: number) {
    if (typeof EventSource === "undefined") {
      void pollTimeline(caseId, 0, currentRequest);
      return;
    }
    const source = new EventSource(`/api/demo/cases/${caseId}/events`);
    streamRef.current = source;
    let lastSequence = 0;
    source.addEventListener("trace", (message) => {
      if (currentRequest !== requestId.current) return;
      const event = JSON.parse((message as MessageEvent).data) as TraceEvent;
      lastSequence = event.sequence;
      setTimeline((current) => [...current, event]);
    });
    source.addEventListener("done", () => source.close());
    source.onerror = () => {
      source.close();
      if (currentRequest === requestId.current) void pollTimeline(caseId, lastSequence, currentRequest);
    };
  }

  async function openCase(caseId: string) {
    const currentRequest = ++requestId.current;
    streamRef.current?.close();
    setTimeline([]);
    setTraceError(false);
    setSelectedCaseId(caseId);
    setCaseError(null);
    setReplay(null);
    setSelectedEvidence(null);
    try {
      const response = await fetch(`/api/demo/cases/${caseId}`);
      if (currentRequest !== requestId.current) return;
      if (!response.ok) {
        const body = await response.json() as { detail?: string };
        if (currentRequest === requestId.current) setCaseError(body.detail ?? "Could not load investigation. Please try again.");
        return;
      }
      const body = await response.json() as Replay;
      if (currentRequest !== requestId.current) return;
      setReplay(body);
      replayTimeline(caseId, currentRequest);
    } catch {
      if (currentRequest === requestId.current) setCaseError("Could not load investigation. Please try again.");
    }
  }

  async function loadCases() {
    try {
      const response = await fetch("/api/demo/cases");
      if (!response.ok) throw new Error("Could not load cases");
      const body = await response.json() as { cases?: DemoCase[] };
      setCases(body.cases ?? []);
      setListError(false);
    } catch {
      setListError(true);
    }
  }

  useEffect(() => {
    void fetch("/api/health")
      .then((response) => {
        if (!response.ok) {
          throw new Error("IssueLens API is unhealthy");
        }
        return response.json();
      })
      .then((body: { status?: string }) => {
        if (body.status === "ok") {
          setHealth("online");
        } else {
          setHealth("unavailable");
        }
      })
      .catch(() => setHealth("unavailable"));
  }, []);

  useEffect(() => {
    void loadCases();
  }, []);

  return (
    <main className="demo-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">ISSUELENS / CONTROLLED DEMO</p>
          <h1>IssueLens</h1>
          <p className="hero-copy">Evidence-driven investigation for open-source issues.</p>
          <p className="hero-note">Five fixed Starlette scenarios. Every finding leads back to evidence.</p>
        </div>
        <p className={`health health-${health}`} aria-live="polite">{statusMessage[health]}</p>
      </header>
      <section className="case-panel" aria-label="Curated investigations">
        <div className="panel-heading"><span>01 / SELECT A CASE</span><strong>Five investigations</strong></div>
        {listError && <div role="alert">
          <p>Could not load demo cases. Please retry.</p>
          <button type="button" onClick={() => void loadCases()}>Retry loading cases</button>
        </div>}
        {cases.map((demoCase, index) => (
          <article className={`case-card ${selectedCaseId === demoCase.case_id ? "is-active" : ""}`} key={demoCase.case_id}>
            <div className="case-meta"><span>0{index + 1} / STARLETTE</span><span className={`outcome outcome-${demoCase.outcome}`}>{demoCase.outcome.replaceAll("_", " ")}</span></div>
            <h2>{demoCase.title}</h2>
            <p className="scenario">{demoCase.scenario}</p>
            <button type="button" aria-label={`View investigation: ${demoCase.title}`} onClick={() => void openCase(demoCase.case_id)}>
              View investigation <span aria-hidden="true">↗</span>
            </button>
          </article>
        ))}
      </section>
      {caseError && <div className="case-error" role="alert">
        <p>{caseError}</p>
        {selectedCaseId && <button type="button" onClick={() => void openCase(selectedCaseId)}>Retry investigation</button>}
        <button type="button" onClick={() => { setCaseError(null); setReplay(null); }}>Back to cases</button>
      </div>}
      {!replay && !caseError && <section className="empty-panel" aria-label="Demo introduction">
        <span className="empty-mark">↗</span>
        <p className="eyebrow">READ THE TRACE</p>
        <h2>Choose a case to inspect the investigation.</h2>
        <p>Follow the timeline, read the report, open fixed-commit evidence, and inspect offline patch validation where available.</p>
        <p className="empty-foot">PREGENERATED REPLAY · NO LIVE CODE EXECUTION</p>
      </section>}
      {replay && (
        <section className="report-panel" aria-label="Investigation report">
          <p className="eyebrow">02 / INVESTIGATION REPORT</p>
          <h2>{replay.case.title}</h2>
          <div className="report-status"><span className={`outcome outcome-${replay.run.outcome.status}`}>{replay.run.outcome.status.replaceAll("_", " ")}</span><code>{replay.run.outcome.stop_reason}</code></div>
          <p className="outcome-copy">{outcomeMessages[replay.run.outcome.status]}</p>
          <p className="scenario">{replay.case.scenario}</p>
          <section className="timeline" aria-label="Investigation timeline">
            <h3>Trace timeline</h3>
            {traceError && <div role="alert">
              <p>Trace replay is unavailable. Please retry or choose another case.</p>
              <button type="button" onClick={() => void pollTimeline(replay.case.case_id, timeline.at(-1)?.sequence ?? 0)}>Retry trace</button>
            </div>}
            <ol>{timeline.map((event) => (
              <li key={event.sequence}>
                <span>{String(event.sequence).padStart(2, "0")}</span>
                <div>{event.kind === "tool" ? `Tool · ${event.tool}` : `Stage · ${event.stage ?? event.kind}`}</div>
              </li>
            ))}</ol>
          </section>
          <h3 className="section-title">Confirmed facts</h3>
          {replay.run.report.facts.map((claim, index) => <article className="claim" key={index}>
            <p>{claim.text}</p>
            {claim.evidence.map((evidence, evidenceIndex) => <button className="citation" key={evidenceIndex} type="button" onClick={() => setSelectedEvidence(evidence)}>
              {evidence.path}:{evidence.start_line} · {evidence.commit.slice(0, 8)}
            </button>)}
          </article>)}
          <h3 className="section-title">Root cause hypotheses</h3>
          {replay.run.report.root_cause_hypotheses.map((claim, index) => (
            <article className="claim" key={index}>
              <p>{claim.text}</p>
              {claim.evidence.map((evidence, evidenceIndex) => (
                <button className="citation" key={evidenceIndex} type="button" onClick={() => setSelectedEvidence(evidence)}>
                  {evidence.path}:{evidence.start_line} · {evidence.commit.slice(0, 8)}
                </button>
              ))}
            </article>
          ))}
          <h3 className="section-title">Open questions</h3>
          <ul>{replay.run.report.unresolved_questions.map((question, index) => <li key={index}>{question}</li>)}</ul>
          {replay.patch_validation?.attempts.map((attempt, index) => (
            <section className="patch-panel" key={index} aria-label="Patch validation">
              <p className="eyebrow">SEPARATE OFFLINE VALIDATION CAMPAIGN</p>
              <h3>Candidate patch</h3>
              <p>{attempt.candidate_origin === "fixed_candidate" ? "Fixed candidate" : attempt.candidate_origin === "reference_repair" ? "Reference repair used for evaluation" : attempt.candidate_origin}</p>
              <p>Target test {attempt.target_passed ? "passed" : "did not pass"}</p>
              <p>Regression tests {attempt.regression_passed ? "passed" : "did not pass"}</p>
              {attempt.diff && <pre>{attempt.diff}</pre>}
            </section>
          ))}
        </section>
      )}
      {selectedEvidence && (
        <aside className="evidence-panel" aria-label="Evidence detail">
          <p className="eyebrow">03 / SOURCE EVIDENCE</p>
          <h2>{selectedEvidence.path}:{selectedEvidence.start_line}-{selectedEvidence.end_line}</h2>
          <p>Commit {selectedEvidence.commit}</p>
          <pre>{selectedEvidence.excerpt}</pre>
          <a href={selectedEvidence.reference} target="_blank" rel="noreferrer">Open fixed commit and lines</a>
        </aside>
      )}
    </main>
  );
}

const statusMessage: Record<HealthState, string> = {
  checking: "Checking service status…",
  online: "All systems operational",
  unavailable: "Service unavailable — try again later",
};
