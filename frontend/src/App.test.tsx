import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
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
