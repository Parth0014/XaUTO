import { useEffect, useMemo, useState } from "react";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const INITIAL_SCRAPE_STATE = {
  state: "idle",
  message: "Waiting to start scraping.",
  chrome: "unknown",
  cycle: 0,
  inserted: 0,
  seen: 0,
  last_topic: null,
  last_author: null,
  last_content: null,
  last_error: null,
  started_at: null,
  updated_at: null,
};

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
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

function normalizeCountMap(data, labelKey) {
  if (!data || typeof data !== "object") {
    return [];
  }

  return Object.entries(data)
    .map(([label, count]) => ({
      [labelKey]: label,
      count: Number(count) || 0,
    }))
    .sort((left, right) => right.count - left.count);
}

function formatTimestamp(value) {
  if (!value) {
    return "—";
  }

  const parsed = new Date(value);

  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }

  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    month: "short",
    day: "numeric",
  }).format(parsed);
}

function truncateText(value, length = 140) {
  if (!value) {
    return "—";
  }

  return value.length > length ? `${value.slice(0, length).trim()}…` : value;
}

function getStatusTone(state) {
  if (state === "running") {
    return "live";
  }

  if (state === "error") {
    return "error";
  }

  return "idle";
}

export default function ReviewDashboard() {
  const [topic, setTopic] = useState("programming");
  const [topPosts, setTopPosts] = useState([]);
  const [topics, setTopics] = useState([]);
  const [sentiment, setSentiment] = useState([]);
  const [scrapeState, setScrapeState] = useState(INITIAL_SCRAPE_STATE);
  const [topPostsLoading, setTopPostsLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [isScraping, setIsScraping] = useState(false);
  const [isRunningPipeline, setIsRunningPipeline] = useState(false);
  const [batchCount, setBatchCount] = useState(1);

  async function fetchAnalytics() {
    try {
      const [topPostsData, topicsData, sentimentData] = await Promise.all([
        apiRequest("/analytics/top-posts"),
        apiRequest("/analytics/topics"),
        apiRequest("/analytics/sentiment"),
      ]);

      setTopPosts(Array.isArray(topPostsData) ? topPostsData : []);
      setTopics(normalizeCountMap(topicsData, "topic"));
      setSentiment(normalizeCountMap(sentimentData, "sentiment"));
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to load analytics",
      );
    } finally {
      setTopPostsLoading(false);
    }
  }

  async function fetchScrapeState() {
    try {
      const data = await apiRequest("/scrape/status");
      setScrapeState((previous) => ({
        ...previous,
        ...data,
      }));
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to load scraping status",
      );
    }
  }

  useEffect(() => {
    let mounted = true;

    const loadInitialData = async () => {
      await Promise.all([fetchAnalytics(), fetchScrapeState()]);
    };

    loadInitialData().catch(() => {
      if (mounted) {
        setError("Failed to initialize dashboard data");
      }
    });

    const intervalId = window.setInterval(() => {
      fetchScrapeState();
      fetchAnalytics();
    }, 2500);

    return () => {
      mounted = false;
      window.clearInterval(intervalId);
    };
  }, []);

  async function generatePost() {
    try {
      setIsGenerating(true);
      setError("");
      setActionMessage("");
      const count = Math.max(1, Math.min(11, Number(batchCount) || 1));
      const response = await apiRequest(
        `/generate/${encodeURIComponent(topic)}?count=${count}`,
      );
      const results = Array.isArray(response?.items) ? response.items : [];

      setActionMessage(
        `Generated ${results.length} post${results.length > 1 ? "s" : ""} for ${topic}. Sent to the autonomous pipeline.`,
      );
      await Promise.all([fetchAnalytics(), fetchScrapeState()]);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to generate a draft",
      );
    } finally {
      setIsGenerating(false);
    }
  }

  async function startScraper() {
    try {
      setIsScraping(true);
      setError("");
      setActionMessage("");
      const result = await apiRequest("/scrape/x");
      setActionMessage(result.message ?? "X scraping started");
      await fetchScrapeState();
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to start the scraper",
      );
    } finally {
      setIsScraping(false);
      await Promise.all([fetchAnalytics(), fetchScrapeState()]);
    }
  }

  async function runPipeline() {
    try {
      setIsRunningPipeline(true);
      setError("");
      setActionMessage("");
      const result = await apiRequest("/pipeline/run", { method: "POST" });
      const posted = Number(result?.posting?.posted ?? 0);
      const failed = Array.isArray(result?.posting?.failed)
        ? result.posting.failed.length
        : 0;
      setActionMessage(
        `Pipeline complete: embedded ${result?.embedded ?? 0}, scored ${result?.scoring?.scored ?? 0}, posted ${posted}, failed ${failed}.`,
      );
      await Promise.all([fetchAnalytics(), fetchScrapeState()]);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to run the full pipeline",
      );
    } finally {
      setIsRunningPipeline(false);
    }
  }

  const stats = useMemo(() => {
    const safeTopPosts = Array.isArray(topPosts) ? topPosts : [];
    const safeTopics = Array.isArray(topics) ? topics : [];
    const safeSentiment = Array.isArray(sentiment) ? sentiment : [];

    const totalTopPosts = safeTopPosts.length;
    const dominantTopic = safeTopics[0]?.topic ?? "None";
    const totalSentiment = safeSentiment.reduce(
      (sum, entry) => sum + entry.count,
      0,
    );
    const positiveShare =
      safeSentiment.find(
        (entry) => String(entry.sentiment).toLowerCase() === "positive",
      )?.count ?? 0;

    return {
      latestTopic: scrapeState.last_topic ?? "None",
      topPosts: totalTopPosts,
      dominantTopic,
      positiveShare: totalSentiment === 0 ? 0 : positiveShare,
      scrapeState: scrapeState.state,
      scrapeInserted: scrapeState.inserted ?? 0,
      scrapeSeen: scrapeState.seen ?? 0,
    };
  }, [topPosts, topics, sentiment, scrapeState]);

  const maxTopicCount = Math.max(...topics.map((item) => item.count), 1);
  const maxSentimentCount = Math.max(...sentiment.map((item) => item.count), 1);

  const activityItems = [
    {
      label: "Scrape status",
      value: scrapeState.message,
      detail: `State: ${scrapeState.state}`,
    },
    {
      label: "Source",
      value: "X API",
      detail: "Recent search via OAuth 1.0a",
    },
    {
      label: "Latest capture",
      value:
        scrapeState.last_author && scrapeState.last_topic
          ? `${scrapeState.last_author} · ${scrapeState.last_topic}`
          : "No post captured yet",
      detail: truncateText(scrapeState.last_content, 110),
    },
    {
      label: "Last error",
      value: scrapeState.last_error ?? "None",
      detail: scrapeState.last_error
        ? "Check the scraper logs and retry the flow."
        : "No parsing or connection errors reported.",
    },
  ];

  return (
    <main className="dashboard-shell">
      <section className="hero-panel hero-panel--wide">
        <div className="hero-copy">
          <div className="eyebrow">X AI Operations</div>
          <h1>Live scraping, generation, and analytics in one place.</h1>
          <p>
            Run the end-to-end pipeline, watch scraping progress in real time,
            generate posts from the latest data, and monitor the system as it
            runs autonomously.
          </p>
          <div className="hero-metrics">
            <div>
              <span>Scrape state</span>
              <strong>{stats.scrapeState}</strong>
            </div>
            <div>
              <span>Rows inserted</span>
              <strong>{stats.scrapeInserted}</strong>
            </div>
            <div>
              <span>Visible posts scanned</span>
              <strong>{stats.scrapeSeen}</strong>
            </div>
          </div>
        </div>

        <div className="hero-actions stack-actions">
          <button
            className="button button-primary"
            onClick={startScraper}
            disabled={isScraping}
          >
            {isScraping ? "Starting scraper..." : "Start scraper"}
          </button>
          <button
            className="button button-primary"
            onClick={runPipeline}
            disabled={isRunningPipeline}
          >
            {isRunningPipeline ? "Running pipeline..." : "Run full pipeline"}
          </button>
          <button
            className="button button-secondary"
            onClick={generatePost}
            disabled={isGenerating}
          >
            {isGenerating ? "Generating..." : `Generate ${topic}`}
          </button>
          <button className="button button-ghost" onClick={fetchScrapeState}>
            Refresh live status
          </button>
        </div>
      </section>

      <section className="stats-grid stats-grid--compact">
        <article className="stat-card">
          <span>Latest topic</span>
          <strong>{stats.latestTopic}</strong>
        </article>
        <article className="stat-card">
          <span>Top posts loaded</span>
          <strong>{stats.topPosts}</strong>
        </article>
        <article className="stat-card">
          <span>Dominant topic</span>
          <strong>{stats.dominantTopic}</strong>
        </article>
        <article className="stat-card">
          <span>Positive sentiment</span>
          <strong>{stats.positiveShare}</strong>
        </article>
      </section>

      {actionMessage ? (
        <div className="success-banner">{actionMessage}</div>
      ) : null}

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="workspace-grid">
        <article className="panel-card live-panel">
          <div className="panel-header panel-header--stacked">
            <div>
              <div className="queue-label">Live scrape monitor</div>
              <h2>Real-time scraper telemetry</h2>
            </div>
            <span
              className={`status-pill status-pill--${getStatusTone(scrapeState.state)}`}
            >
              {scrapeState.state === "running"
                ? "Scraping live"
                : scrapeState.state === "error"
                  ? "Scrape error"
                  : "Idle"}
            </span>
          </div>

          <div className="live-summary-grid">
            <div className="live-summary-card">
              <span>Cycle</span>
              <strong>{scrapeState.cycle ?? 0}</strong>
            </div>
            <div className="live-summary-card">
              <span>Inserted this run</span>
              <strong>{scrapeState.inserted ?? 0}</strong>
            </div>
            <div className="live-summary-card">
              <span>Cards scanned</span>
              <strong>{scrapeState.seen ?? 0}</strong>
            </div>
            <div className="live-summary-card">
              <span>Source</span>
              <strong>X API</strong>
            </div>
          </div>

          <div className="live-detail-grid">
            <div>
              <span>Started</span>
              <strong>{formatTimestamp(scrapeState.started_at)}</strong>
            </div>
            <div>
              <span>Updated</span>
              <strong>{formatTimestamp(scrapeState.updated_at)}</strong>
            </div>
            <div>
              <span>Last author</span>
              <strong>{scrapeState.last_author ?? "—"}</strong>
            </div>
            <div>
              <span>Last topic</span>
              <strong>{scrapeState.last_topic ?? "—"}</strong>
            </div>
          </div>

          <div className="live-note">
            <span>Current message</span>
            <p>{scrapeState.message}</p>
          </div>

          <div className="timeline-card">
            <div className="timeline-header">
              <strong>Event feed</strong>
              <span>Updates every 2.5 seconds</span>
            </div>
            <div className="timeline-list">
              {activityItems.map((item) => (
                <div className="timeline-item" key={item.label}>
                  <div>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </div>
                  <p>{item.detail}</p>
                </div>
              ))}
            </div>
          </div>
        </article>

        <div className="sidebar-stack">
          <article className="panel-card">
            <div className="panel-header panel-header--stacked">
              <div>
                <div className="queue-label">Generation control</div>
                <h2>Topic input and actions</h2>
              </div>
            </div>

            <div className="toolbar toolbar--stacked">
              <input
                className="topic-input"
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="Topic, e.g. programming"
              />
              <div className="queue-toolbar">
                <input
                  className="topic-input"
                  type="number"
                  min="1"
                  max="11"
                  value={batchCount}
                  onChange={(event) => setBatchCount(event.target.value)}
                  placeholder="Batch count"
                />
                <span className="queue-hint">Max 11 per batch</span>
              </div>
            </div>

            <p className="panel-note">
              The dashboard polls scrape status and analytics in the background
              while generation runs autonomously.
            </p>
          </article>

          <article className="panel-card">
            <div className="panel-header panel-header--stacked">
              <div>
                <div className="queue-label">Analytics snapshot</div>
                <h2>Top performing posts</h2>
              </div>
              <span className="status-pill">Live from backend</span>
            </div>

            {topPostsLoading ? (
              <div className="empty-state">Loading analytics...</div>
            ) : topPosts.length === 0 ? (
              <div className="empty-state">No analytics data yet.</div>
            ) : (
              <div className="mini-list">
                {topPosts.slice(0, 4).map((post, index) => (
                  <article
                    className="mini-card"
                    key={`${post.author}-${index}`}
                  >
                    <div className="mini-card-top">
                      <strong>{post.author}</strong>
                      <span>{post.topic ?? "general"}</span>
                    </div>
                    <p>{truncateText(post.content, 165)}</p>
                    <div className="mini-meta">
                      <span>Likes {post.likes}</span>
                      <span>Views {post.views}</span>
                      <span>{post.sentiment}</span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </article>
        </div>
      </section>

      <section className="content-grid content-grid--balanced">
        <article className="panel-card">
          <div className="panel-header panel-header--stacked">
            <div>
              <div className="queue-label">Topic distribution</div>
              <h2>Scraped topic mix</h2>
            </div>
          </div>

          {topics.length === 0 ? (
            <div className="empty-state">No topic data available yet.</div>
          ) : (
            <div className="mini-list">
              {topics.map((entry) => (
                <div className="bar-row" key={entry.topic}>
                  <div className="bar-row-head">
                    <span>{entry.topic}</span>
                    <strong>{entry.count}</strong>
                  </div>
                  <div className="bar-track">
                    <div
                      className="bar-fill"
                      style={{
                        width: `${Math.max(8, Math.min(100, (entry.count / maxTopicCount) * 100))}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </article>

        <article className="panel-card">
          <div className="panel-header panel-header--stacked">
            <div>
              <div className="queue-label">Sentiment mix</div>
              <h2>Model output by sentiment</h2>
            </div>
          </div>

          {sentiment.length === 0 ? (
            <div className="empty-state">No sentiment data available yet.</div>
          ) : (
            <div className="mini-list">
              {sentiment.map((entry) => (
                <div className="bar-row" key={entry.sentiment}>
                  <div className="bar-row-head">
                    <span>{entry.sentiment}</span>
                    <strong>{entry.count}</strong>
                  </div>
                  <div className="bar-track">
                    <div
                      className="bar-fill bar-fill-alt"
                      style={{
                        width: `${Math.max(8, Math.min(100, (entry.count / maxSentimentCount) * 100))}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </article>
      </section>
    </main>
  );
}
