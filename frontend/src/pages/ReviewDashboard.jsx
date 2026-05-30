import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const PIPELINE_STAGE_ORDER = [
  "scrape",
  "generate",
  "db",
  "preprocess",
  "score",
  "post",
  "complete",
  "error",
];

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
    ...options,
  });

  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail =
      typeof payload === "string"
        ? payload
        : (payload?.detail ?? `Request failed with status ${response.status}`);
    throw new Error(detail);
  }

  return payload;
}

function formatTimestamp(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    month: "short",
    day: "numeric",
  }).format(parsed);
}

function getEventLabel(type) {
  const map = {
    scrape_progress: "Scrape",
    scoring: "Score",
    post_feedback: "Feedback",
    pipeline: "Pipeline",
    request: "Request",
  };
  return map[type] ?? "Event";
}

function getEventSummary(entry) {
  const payload = entry?.body ?? {};

  if (entry.type === "pipeline") {
    const stage = payload.stage ? `${payload.stage}: ` : "";
    const status = payload.status ? `${payload.status} - ` : "";
    return `${stage}${status}${payload.message ?? "Pipeline event received."}`;
  }

  if (entry.type === "scrape_progress") {
    return `${payload.message ?? "Scrape progress updated."} (${payload.state ?? "unknown"}, inserted ${payload.inserted ?? 0}, seen ${payload.seen ?? 0})`;
  }

  if (entry.type === "scoring") {
    return `Post ${payload.post_id ?? "unknown"}: predicted ${payload.predicted_score ?? "n/a"}, reward ${payload.reward_score ?? "n/a"}`;
  }

  if (entry.type === "post_feedback") {
    return `Updated engagement for ${payload.updated ?? 0} posted item${payload.updated === 1 ? "" : "s"}`;
  }

  if (entry.type === "request") {
    return `${payload.method ?? "GET"} ${payload.path ?? "unknown path"} responded with ${payload.status_code ?? "n/a"}`;
  }

  return payload?.message ?? "Live event received.";
}

function badgeStyles(variant) {
  if (variant === "live")
    return { background: "rgba(34,197,94,.12)", color: "#16a34a" };
  if (variant === "error")
    return { background: "rgba(239,68,68,.12)", color: "#dc2626" };
  return { background: "rgba(100,116,139,.12)", color: "#64748b" };
}

function Panel({ title, subtitle, right, children }) {
  return (
    <section
      style={{
        border: "1px solid rgba(148,163,184,.25)",
        borderRadius: 16,
        overflow: "hidden",
        background: "rgba(15,23,42,.72)",
        backdropFilter: "blur(10px)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 16,
          alignItems: "center",
          padding: "14px 16px",
          borderBottom: "1px solid rgba(148,163,184,.18)",
        }}
      >
        <div>
          <div style={{ fontWeight: 700, color: "#e2e8f0" }}>{title}</div>
          {subtitle ? (
            <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>
              {subtitle}
            </div>
          ) : null}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

function StatusChip({ backendReady, status }) {
  const variant =
    status === "running" ? "live" : status === "error" ? "error" : "idle";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "6px 10px",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 600,
        ...badgeStyles(variant),
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: 999,
          background:
            variant === "live"
              ? "#22c55e"
              : variant === "error"
                ? "#ef4444"
                : "#94a3b8",
        }}
      />
      {backendReady ? "Backend ready" : "Connecting…"}
    </span>
  );
}

export default function ReviewDashboard() {
  const [backendReady, setBackendReady] = useState(false);
  const [scrapeState, setScrapeState] = useState({
    state: "idle",
    message: "Waiting for live events.",
  });
  const [logs, setLogs] = useState([]);
  const feedRef = useRef(null);
  const wasAtBottomRef = useRef(true);

  useEffect(() => {
    let mounted = true;
    let retryTimer;
    let intervalId;
    let eventSource;

    const connect = async () => {
      try {
        await apiRequest("/healthz");
        if (!mounted) return;

        setBackendReady(true);
        try {
          const data = await apiRequest("/scrape/status");
          if (mounted) setScrapeState((prev) => ({ ...prev, ...(data ?? {}) }));
        } catch {
          // ignore scrape status failures during initial connect
        }

        intervalId = window.setInterval(() => {
          apiRequest("/healthz")
            .then(() => {
              if (mounted) setBackendReady(true);
            })
            .catch(() => {
              if (mounted) setBackendReady(false);
            });
        }, 10000);

        eventSource = new EventSource(`${API_BASE_URL}/events/stream`);
        eventSource.onmessage = (evt) => {
          try {
            const msg = JSON.parse(evt.data);
            const entry = {
              id: Date.now() + Math.random(),
              type: msg?.type ?? "event",
              body: msg?.payload ?? {},
              ts: new Date().toISOString(),
            };

            if (entry.type === "scrape_progress") {
              setScrapeState((prev) => ({ ...prev, ...(entry.body ?? {}) }));
            }

            setLogs((prev) => [entry, ...prev].slice(0, 200));
          } catch {
            // ignore malformed SSE payloads
          }
        };
        eventSource.onerror = () => {
          if (mounted) setBackendReady(false);
        };
      } catch {
        if (!mounted) return;
        setBackendReady(false);
        retryTimer = window.setTimeout(connect, 5000);
      }
    };

    connect();

    return () => {
      mounted = false;
      window.clearTimeout(retryTimer);
      window.clearInterval(intervalId);
      try {
        eventSource?.close();
      } catch {
        // ignore cleanup errors
      }
    };
  }, []);

  const pipelineStages = useMemo(() => {
    const latestByStage = new Map();

    for (const entry of logs.filter((item) => item.type === "pipeline")) {
      const stage = entry?.body?.stage || "unknown";
      latestByStage.set(stage, entry);
    }

    return PIPELINE_STAGE_ORDER.map((stage) => {
      const entry = latestByStage.get(stage);
      return {
        stage,
        status: entry?.body?.status ?? "waiting",
        message: entry?.body?.message ?? "Waiting for pipeline run.",
        ts: entry?.ts ?? null,
      };
    });
  }, [logs]);

  const recentLogs = logs;

  useEffect(() => {
    const el = feedRef.current;
    if (!el) return;
    wasAtBottomRef.current =
      el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
  });

  useEffect(() => {
    const el = feedRef.current;
    if (el && wasAtBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [logs]);

  return (
    <main
      style={{
        minHeight: "100vh",
        padding: 24,
        background:
          "radial-gradient(circle at top, rgba(56,189,248,.12), transparent 32%), linear-gradient(180deg, #0f172a 0%, #111827 100%)",
        color: "#e2e8f0",
      }}
    >
      <div style={{ maxWidth: 1180, margin: "0 auto" }}>
        <Panel
          title="Pipeline monitor"
          subtitle="Only the ordered pipeline checklist, live log feed, and readiness indicator"
          right={
            <StatusChip
              backendReady={backendReady}
              status={scrapeState.state}
            />
          }
        >
          <div
            style={{
              padding: 16,
              display: "grid",
              gap: 16,
              gridTemplateColumns: "1.1fr .9fr",
            }}
          >
            <section style={{ display: "grid", gap: 12 }}>
              <div style={{ display: "grid", gap: 10 }}>
                <div style={{ fontSize: 12, color: "#94a3b8" }}>
                  Ordered pipeline
                </div>
                <div style={{ display: "grid", gap: 8 }}>
                  {pipelineStages.length === 0 ? (
                    <div
                      style={{
                        padding: 14,
                        borderRadius: 12,
                        border: "1px dashed rgba(148,163,184,.35)",
                        color: "#94a3b8",
                      }}
                    >
                      No pipeline stages received yet.
                    </div>
                  ) : (
                    pipelineStages.map((stage) => (
                      <div
                        key={stage.stage}
                        style={{
                          display: "flex",
                          alignItems: "flex-start",
                          gap: 12,
                          padding: 12,
                          borderRadius: 12,
                          border: "1px solid rgba(148,163,184,.18)",
                          background: "rgba(15,23,42,.5)",
                        }}
                      >
                        <div
                          style={{
                            minWidth: 86,
                            display: "inline-flex",
                            justifyContent: "center",
                            alignItems: "center",
                            padding: "4px 10px",
                            borderRadius: 999,
                            fontSize: 11,
                            fontWeight: 700,
                            textTransform: "uppercase",
                            ...(stage.status === "complete"
                              ? badgeStyles("live")
                              : stage.status === "error" ||
                                  stage.status === "failed"
                                ? badgeStyles("error")
                                : badgeStyles("idle")),
                          }}
                        >
                          {stage.stage}
                        </div>
                        <div style={{ flex: 1 }}>
                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              gap: 12,
                              alignItems: "center",
                            }}
                          >
                            <strong style={{ fontSize: 13 }}>
                              {stage.status}
                            </strong>
                            <span style={{ fontSize: 11, color: "#94a3b8" }}>
                              {formatTimestamp(stage.ts)}
                            </span>
                          </div>
                          <div
                            style={{
                              marginTop: 4,
                              fontSize: 13,
                              lineHeight: 1.5,
                              color: "#cbd5e1",
                            }}
                          >
                            {stage.message}
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div style={{ fontSize: 12, color: "#94a3b8" }}>
                {backendReady
                  ? "Backend connected. Events will stream live."
                  : "Waiting for backend health check."}
              </div>
            </section>

            <section>
              <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 10 }}>
                Live log feed
              </div>
              <div
                ref={feedRef}
                style={{
                  height: 520,
                  overflowY: "auto",
                  borderRadius: 12,
                  border: "1px solid rgba(148,163,184,.18)",
                  background: "rgba(2,6,23,.35)",
                }}
              >
                {recentLogs.length === 0 ? (
                  <div style={{ padding: 18, color: "#94a3b8" }}>
                    No events yet. Waiting for SSE stream…
                  </div>
                ) : (
                  recentLogs.map((entry) => (
                    <div
                      key={entry.id}
                      style={{
                        display: "flex",
                        gap: 10,
                        alignItems: "flex-start",
                        padding: "12px 14px",
                        borderBottom: "1px solid rgba(148,163,184,.12)",
                      }}
                    >
                      <div
                        style={{
                          width: 26,
                          height: 26,
                          borderRadius: 8,
                          display: "grid",
                          placeItems: "center",
                          fontSize: 10,
                          fontWeight: 800,
                          color: "#fff",
                          background:
                            entry.type === "pipeline"
                              ? "rgba(59,130,246,.35)"
                              : entry.type === "scrape_progress"
                                ? "rgba(34,197,94,.35)"
                                : entry.type === "scoring"
                                  ? "rgba(245,158,11,.35)"
                                  : entry.type === "post_feedback"
                                    ? "rgba(14,165,233,.35)"
                                    : "rgba(148,163,184,.35)",
                        }}
                      >
                        {getEventLabel(entry.type).slice(0, 2).toUpperCase()}
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            gap: 10,
                            alignItems: "center",
                          }}
                        >
                          <span
                            style={{
                              fontSize: 11,
                              fontWeight: 700,
                              letterSpacing: "0.04em",
                              textTransform: "uppercase",
                              color: "#93c5fd",
                            }}
                          >
                            {getEventLabel(entry.type)}
                          </span>
                          <span style={{ fontSize: 11, color: "#94a3b8" }}>
                            {formatTimestamp(entry.ts)}
                          </span>
                        </div>
                        <div
                          style={{
                            marginTop: 4,
                            fontSize: 13,
                            lineHeight: 1.5,
                            color: "#e2e8f0",
                            wordBreak: "break-word",
                          }}
                        >
                          {getEventSummary(entry)}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </section>
          </div>
        </Panel>
      </div>
    </main>
  );
}
