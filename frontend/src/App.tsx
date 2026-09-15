import { useEffect, useState } from "react";

type HealthState = "checking" | "online" | "unavailable";

export function App() {
  const [health, setHealth] = useState<HealthState>("checking");

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

  return (
    <main>
      <h1>IssueLens</h1>
      <p>Evidence-driven investigation for open-source issues.</p>
      <p aria-live="polite">{statusMessage[health]}</p>
    </main>
  );
}

const statusMessage: Record<HealthState, string> = {
  checking: "Checking service status…",
  online: "All systems operational",
  unavailable: "Service unavailable — try again later",
};
