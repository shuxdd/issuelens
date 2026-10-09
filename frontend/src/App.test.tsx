import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("shows that IssueLens is online when the API is healthy", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ service: "issuelens-api", status: "ok" }), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    }),
  );

  render(<App />);

  expect(await screen.findByText("All systems operational")).toBeInTheDocument();
});

test("shows that IssueLens is unavailable when the health request fails", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("API unavailable"));

  render(<App />);

  expect(await screen.findByText("Service unavailable — try again later")).toBeInTheDocument();
});

test("shows that IssueLens is unavailable when the API reports an unhealthy status", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ service: "issuelens-api", status: "error" }), {
      headers: { "Content-Type": "application/json" },
      status: 503,
    }),
  );

  render(<App />);

  expect(await screen.findByText("Service unavailable — try again later")).toBeInTheDocument();
});


test("shows the five curated investigation cases", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (input === "/api/health") {
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    }
    return new Response(JSON.stringify({ cases: [
      { case_id: "starlette-issue-1552", title: "Route names", outcome: "supported", scenario: "Recorded" },
      { case_id: "starlette-issue-886", title: "Async callable", outcome: "supported", scenario: "Recorded" },
      { case_id: "starlette-issue-1355", title: "Evidence gap", outcome: "insufficient_evidence", scenario: "Recorded" },
      { case_id: "starlette-issue-1552-budget", title: "Budget limit", outcome: "budget_exhausted", scenario: "Offline" },
      { case_id: "safe-dangerous-01", title: "Unsafe request", outcome: "unsafe_request", scenario: "Offline" },
    ] }), { status: 200 });
  });

  render(<App />);

  expect(await screen.findAllByRole("button", { name: /view investigation/i })).toHaveLength(5);
  expect(screen.getByText("Route names")).toBeInTheDocument();
  expect(screen.getByText("Unsafe request")).toBeInTheDocument();
});

test("keeps the latest investigation when an earlier request finishes later", async () => {
  vi.stubGlobal("EventSource", undefined);
  let finishFirst!: (response: Response) => void;
  const first = new Promise<Response>((resolve) => { finishFirst = resolve; });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (input === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
    if (input === "/api/demo/cases") return new Response(JSON.stringify({ cases: [
      { case_id: "first", title: "First case", outcome: "supported", scenario: "Recorded" },
      { case_id: "second", title: "Second case", outcome: "supported", scenario: "Recorded" },
    ] }));
    if (input === "/api/demo/cases/first") return first;
    if (String(input).includes("/progress/")) return new Response(JSON.stringify({ events: [] }));
    return new Response(JSON.stringify({
      case: { case_id: "second", title: "Second case", outcome: "supported", scenario: "Recorded" },
      run: { outcome: { status: "supported", stop_reason: "done" }, events: [],
        report: { facts: [], root_cause_hypotheses: [], unresolved_questions: [], confidence: "high" } },
      patch_validation: null,
    }));
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "View investigation: First case" }));
  fireEvent.click(screen.getByRole("button", { name: "View investigation: Second case" }));
  expect(within(await screen.findByRole("region", { name: "Investigation report" })).getByRole("heading", { name: "Second case" })).toBeInTheDocument();
  await act(async () => finishFirst(new Response(JSON.stringify({
    case: { case_id: "first", title: "First case", outcome: "supported", scenario: "Recorded" },
    run: { outcome: { status: "supported", stop_reason: "done" }, events: [],
      report: { facts: [], root_cause_hypotheses: [], unresolved_questions: [], confidence: "high" } },
    patch_validation: null,
  }))));
  expect(within(screen.getByRole("region", { name: "Investigation report" })).getByRole("heading", { name: "Second case" })).toBeInTheDocument();
});


test("opens a report conclusion and its fixed-commit evidence excerpt", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (input === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
    if (input === "/api/demo/cases") return new Response(JSON.stringify({ cases: [
      { case_id: "starlette-issue-886", title: "Async callable", outcome: "supported", scenario: "Recorded" },
    ] }));
    if (input === "/api/demo/cases/starlette-issue-886/progress/0") {
      return new Response(JSON.stringify({ events: [], complete: true }));
    }
    return new Response(JSON.stringify({
      case: { case_id: "starlette-issue-886", title: "Async callable", outcome: "supported", scenario: "Recorded" },
      run: { outcome: { status: "supported", stop_reason: "verified_root_cause" }, events: [],
        report: { facts: [], root_cause_hypotheses: [{ text: "The returned coroutine is not awaited.",
          confidence: "high", evidence: [{ commit: "199fc703", path: "starlette/routing.py",
            start_line: 602, end_line: 606, excerpt: "if asyncio.iscoroutinefunction(handler):",
            reference: "https://github.com/Kludex/starlette/blob/199fc703/starlette/routing.py#L602-L606" }] }],
          unresolved_questions: [], confidence: "high" } },
      patch_validation: null,
    }));
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "View investigation: Async callable" }));

  expect(await screen.findByText("The returned coroutine is not awaited.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /routing.py:602/ }));
  expect(screen.getByText("if asyncio.iscoroutinefunction(handler):")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /open fixed commit/i })).toHaveAttribute(
    "href", "https://github.com/Kludex/starlette/blob/199fc703/starlette/routing.py#L602-L606",
  );
});


test("shows the recorded candidate patch and separate test results", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (input === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
    if (input === "/api/demo/cases") return new Response(JSON.stringify({ cases: [
      { case_id: "starlette-issue-1552", title: "Route names", outcome: "supported", scenario: "Recorded" },
    ] }));
    if (input === "/api/demo/cases/starlette-issue-1552/progress/0") {
      return new Response(JSON.stringify({ events: [], complete: true }));
    }
    return new Response(JSON.stringify({
      case: { case_id: "starlette-issue-1552", title: "Route names", outcome: "supported", scenario: "Recorded" },
      run: { outcome: { status: "supported", stop_reason: "verified_root_cause" }, events: [],
        report: { facts: [], root_cause_hypotheses: [], unresolved_questions: [], confidence: "high" } },
      patch_validation: { attempts: [{ candidate_origin: "fixed_candidate", diff: "+ return endpoint.__name__",
        target_passed: true, regression_passed: true }] },
    }));
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "View investigation: Route names" }));

  expect(await screen.findByText("Candidate patch")).toBeInTheDocument();
  expect(screen.getByText("+ return endpoint.__name__")).toBeInTheDocument();
  expect(screen.getByText("Target test passed")).toBeInTheDocument();
  expect(screen.getByText("Regression tests passed")).toBeInTheDocument();
});


test("falls back to polling when the trace stream fails", async () => {
  class FailedStream {
    onerror: (() => void) | null = null;
    constructor() { queueMicrotask(() => this.onerror?.()); }
    addEventListener() {}
    close() {}
  }
  vi.stubGlobal("EventSource", FailedStream);
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (input === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
    if (input === "/api/demo/cases") return new Response(JSON.stringify({ cases: [
      { case_id: "starlette-issue-886", title: "Async callable", outcome: "supported", scenario: "Recorded" },
    ] }));
    if (input === "/api/demo/cases/starlette-issue-886/progress/0") return new Response(JSON.stringify({
      events: [{ sequence: 1, kind: "tool", tool: "search_code", status: "ok" }], complete: true,
    }));
    return new Response(JSON.stringify({
      case: { case_id: "starlette-issue-886", title: "Async callable", outcome: "supported", scenario: "Recorded" },
      run: { outcome: { status: "supported", stop_reason: "verified_root_cause" }, events: [],
        report: { facts: [], root_cause_hypotheses: [], unresolved_questions: [], confidence: "high" } },
      patch_validation: null,
    }));
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "View investigation: Async callable" }));

  expect(await screen.findByText("Tool · search_code")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/demo/cases/starlette-issue-886/progress/0");
});


test("offers a retry when the demo case service is unavailable", async () => {
  let listRequests = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (input === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
    listRequests += 1;
    if (listRequests === 1) throw new Error("offline");
    return new Response(JSON.stringify({ cases: [
      { case_id: "starlette-issue-886", title: "Async callable", outcome: "supported", scenario: "Recorded" },
    ] }));
  });

  render(<App />);

  expect(await screen.findByText("Could not load demo cases. Please retry.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry loading cases" }));
  expect(await screen.findByRole("button", { name: "View investigation: Async callable" })).toBeInTheDocument();
});


test("explains an invalid case and offers a return to the curated list", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (input === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
    if (input === "/api/demo/cases") return new Response(JSON.stringify({ cases: [
      { case_id: "missing-case", title: "Unavailable case", outcome: "supported", scenario: "Recorded" },
    ] }));
    return new Response(JSON.stringify({ detail: "Demo case not found. Choose one of the five listed cases." }),
      { status: 404 });
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "View investigation: Unavailable case" }));

  expect(await screen.findByText("Demo case not found. Choose one of the five listed cases.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Back to cases" })).toBeInTheDocument();
});


test.each([
  ["supported", "Evidence supports a root cause hypothesis."],
  ["insufficient_evidence", "The available evidence does not support a root cause."],
  ["budget_exhausted", "The investigation stopped at its resource limit."],
  ["unsafe_request", "The investigation stopped after an unsafe request."],
])("explains the %s outcome in plain English", async (outcome, explanation) => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (input === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
    if (input === "/api/demo/cases") return new Response(JSON.stringify({ cases: [
      { case_id: "scenario", title: "Scenario", outcome, scenario: "Offline" },
    ] }));
    if (input === "/api/demo/cases/scenario/progress/0") {
      return new Response(JSON.stringify({ events: [], complete: true }));
    }
    return new Response(JSON.stringify({
      case: { case_id: "scenario", title: "Scenario", outcome, scenario: "Offline" },
      run: { outcome: { status: outcome, stop_reason: "finished" }, events: [],
        report: { facts: [], root_cause_hypotheses: [], unresolved_questions: [], confidence: "low" } },
      patch_validation: null,
    }));
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "View investigation: Scenario" }));

  expect(await screen.findByText(explanation)).toBeInTheDocument();
});


test("offers a trace retry when both streaming and polling are unavailable", async () => {
  vi.stubGlobal("EventSource", undefined);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (input === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
    if (input === "/api/demo/cases") return new Response(JSON.stringify({ cases: [
      { case_id: "starlette-issue-886", title: "Async callable", outcome: "supported", scenario: "Recorded" },
    ] }));
    if (input === "/api/demo/cases/starlette-issue-886/progress/0") {
      return new Response(JSON.stringify({ detail: "busy" }), { status: 503 });
    }
    return new Response(JSON.stringify({
      case: { case_id: "starlette-issue-886", title: "Async callable", outcome: "supported", scenario: "Recorded" },
      run: { outcome: { status: "supported", stop_reason: "verified_root_cause" }, events: [],
        report: { facts: [], root_cause_hypotheses: [], unresolved_questions: [], confidence: "high" } },
      patch_validation: null,
    }));
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "View investigation: Async callable" }));

  expect(await screen.findByText("Trace replay is unavailable. Please retry or choose another case.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry trace" })).toBeInTheDocument();
});
