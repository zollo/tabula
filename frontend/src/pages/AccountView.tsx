import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import Avatar from "../components/Avatar";
import { useAuth } from "../context/AuthContext";
import {
  BACKFILL_LABELS,
  formatDateTime,
  formatInterval,
  handleLabel,
  INTERVAL_CHOICES,
  KIND_LABELS,
  PLATFORM_LABELS,
} from "../lib/format";
import type { Post, PostKind, PostPage, SocialAccount, Tracking } from "../types";

const PAGE_SIZE = 50;

interface Filters {
  q: string;
  kind: "" | PostKind;
  since: string;
  until: string;
}

function query(filters: Filters, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams(extra);
  if (filters.q) params.set("q", filters.q);
  if (filters.kind) params.set("kind", filters.kind);
  // Dates are whole days in the viewer's time zone.
  if (filters.since) params.set("since", new Date(`${filters.since}T00:00:00`).toISOString());
  if (filters.until) params.set("until", new Date(`${filters.until}T23:59:59`).toISOString());
  return params.toString();
}

export default function AccountView() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [account, setAccount] = useState<SocialAccount | null>(null);
  const [tracking, setTracking] = useState<Tracking | null>(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [total, setTotal] = useState(0);
  const [filters, setFilters] = useState<Filters>({ q: "", kind: "", since: "", until: "" });
  const [applied, setApplied] = useState<Filters>(filters);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fail = (err: unknown, fallback: string) =>
    setError(err instanceof ApiError ? err.message : fallback);

  const loadAccount = useCallback(async () => {
    try {
      setAccount(await api.get<SocialAccount>(`/api/accounts/${id}`));
      const mine = await api.get<Tracking[]>("/api/trackings");
      setTracking(mine.find((t) => t.social_account.id === id) ?? null);
    } catch (err) {
      fail(err, "Failed to load the account");
    }
  }, [id]);

  const loadPosts = useCallback(
    async (offset: number) => {
      setLoading(true);
      try {
        const page = await api.get<PostPage>(
          `/api/accounts/${id}/posts?${query(applied, {
            limit: String(PAGE_SIZE),
            offset: String(offset),
          })}`,
        );
        setTotal(page.total);
        setPosts((prev) => (offset === 0 ? page.items : [...prev, ...page.items]));
      } catch (err) {
        fail(err, "Failed to load posts");
      } finally {
        setLoading(false);
      }
    },
    [id, applied],
  );

  useEffect(() => {
    void loadAccount();
  }, [loadAccount]);

  useEffect(() => {
    void loadPosts(0);
  }, [loadPosts]);

  const updateTracking = async (body: Partial<Pick<Tracking, "scan_interval_minutes" | "is_paused">>) => {
    if (!tracking) return;
    setError(null);
    try {
      setTracking(await api.patch<Tracking>(`/api/trackings/${tracking.id}`, body));
      void loadAccount();
    } catch (err) {
      fail(err, "Update failed");
    }
  };

  const scanNow = async () => {
    if (!tracking) return;
    setError(null);
    try {
      await api.post(`/api/trackings/${tracking.id}/scan`);
      setNotice("Scan queued. New posts will show up in a minute or two.");
    } catch (err) {
      fail(err, "Could not queue a scan");
    }
  };

  const stopTracking = async () => {
    if (!tracking || !account) return;
    if (!confirm(`Stop tracking ${handleLabel(account.platform, account.handle)}?`)) return;
    try {
      await api.del(`/api/trackings/${tracking.id}`);
      navigate("/");
    } catch (err) {
      fail(err, "Could not stop tracking");
    }
  };

  const exportAs = async (format: "json" | "pdf") => {
    if (!account) return;
    setError(null);
    try {
      await api.download(
        `/api/accounts/${id}/export?${query(applied, { format })}`,
        `tabula-${account.platform}-${account.handle}.${format}`,
      );
    } catch (err) {
      fail(err, "Export failed");
    }
  };

  if (!account) {
    return <div className="page">{error ? <div className="alert error">{error}</div> : <p className="muted">Loading…</p>}</div>;
  }

  return (
    <div className="page">
      <div className="page-head">
        <div className="account-row">
          <Avatar account={account} large />
          <div className="who">
            <h1>{account.display_name || account.handle}</h1>
            <div className="muted">
              <span className="platform">{PLATFORM_LABELS[account.platform]}</span>{" "}
              {account.profile_url ? (
                <a href={account.profile_url} target="_blank" rel="noopener noreferrer">
                  {handleLabel(account.platform, account.handle)}
                </a>
              ) : (
                handleLabel(account.platform, account.handle)
              )}
            </div>
          </div>
        </div>
        <div className="row">
          <button className="btn secondary small" onClick={() => void exportAs("json")}>
            Export JSON
          </button>
          <button className="btn small" onClick={() => void exportAs("pdf")}>
            Export PDF
          </button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert success">{notice}</div>}

      <div className="card">
        <dl className="facts">
          <div>
            <dt>Archive</dt>
            <dd>
              {account.post_count.toLocaleString()} posts ·{" "}
              <span className={`badge st-${account.backfill_status}`}>
                {BACKFILL_LABELS[account.backfill_status]}
              </span>
            </dd>
          </div>
          <div>
            <dt>Last scan</dt>
            <dd>{formatDateTime(account.last_scanned_at)}</dd>
          </div>
          <div>
            <dt>Next scan</dt>
            <dd>{account.next_scan_at ? formatDateTime(account.next_scan_at) : "Not scheduled"}</dd>
          </div>
          <div>
            <dt>Scanned</dt>
            <dd>{formatInterval(account.scan_interval_minutes)} (shortest of everyone tracking it)</dd>
          </div>
        </dl>
        {account.last_error && (
          <div className="alert error" style={{ marginTop: 12, marginBottom: 0 }}>
            Last error: {account.last_error}
          </div>
        )}
        {tracking && (
          <>
            <hr className="divider" />
            <div className="row">
              <label htmlFor="interval" style={{ margin: 0 }}>
                Your schedule
              </label>
              <select
                id="interval"
                style={{ width: "auto" }}
                value={tracking.scan_interval_minutes}
                onChange={(e) => void updateTracking({ scan_interval_minutes: Number(e.target.value) })}
              >
                {Array.from(new Set([...INTERVAL_CHOICES, tracking.scan_interval_minutes]))
                  .sort((a, b) => a - b)
                  .map((m) => (
                    <option key={m} value={m}>
                      {formatInterval(m)}
                    </option>
                  ))}
              </select>
              <button
                className="btn secondary small"
                onClick={() => void updateTracking({ is_paused: !tracking.is_paused })}
              >
                {tracking.is_paused ? "Resume" : "Pause"}
              </button>
              {user?.can_track && (
                <button className="btn secondary small" onClick={() => void scanNow()}>
                  Scan now
                </button>
              )}
              <button className="btn danger small" onClick={() => void stopTracking()}>
                Stop tracking
              </button>
            </div>
          </>
        )}
      </div>

      <h2>Posts</h2>
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(filters);
        }}
      >
        <input
          type="search"
          placeholder="Search posts"
          aria-label="Search posts"
          value={filters.q}
          onChange={(e) => setFilters({ ...filters, q: e.target.value })}
        />
        <select
          aria-label="Type"
          value={filters.kind}
          onChange={(e) => {
            const next = { ...filters, kind: e.target.value as Filters["kind"] };
            setFilters(next);
            setApplied(next);
          }}
        >
          <option value="">All types</option>
          {(Object.keys(KIND_LABELS) as PostKind[]).map((k) => (
            <option key={k} value={k}>
              {KIND_LABELS[k]}
            </option>
          ))}
        </select>
        <input
          type="date"
          aria-label="From"
          value={filters.since}
          onChange={(e) => {
            const next = { ...filters, since: e.target.value };
            setFilters(next);
            setApplied(next);
          }}
        />
        <input
          type="date"
          aria-label="To"
          value={filters.until}
          onChange={(e) => {
            const next = { ...filters, until: e.target.value };
            setFilters(next);
            setApplied(next);
          }}
        />
      </form>
      <p className="muted small">
        {total.toLocaleString()} {total === 1 ? "post" : "posts"}. Exports include every post
        matching these filters.
      </p>

      {posts.length === 0 && !loading ? (
        <div className="empty">
          {account.backfill_status === "complete" ? "No posts match." : "Archiving history…"}
        </div>
      ) : (
        <div className="list">
          {posts.map((p) => (
            <article key={p.id} className="post">
              <div className="when">
                <span>{formatDateTime(p.posted_at)}</span>
                {p.kind !== "post" && <span className="badge st-paused">{KIND_LABELS[p.kind]}</span>}
                {p.url && (
                  <a href={p.url} target="_blank" rel="noopener noreferrer">
                    Original
                  </a>
                )}
              </div>
              <div className="body">{p.text || <span className="muted">(no text)</span>}</div>
              {p.media.length > 0 && (
                <div className="media">
                  {p.media.map((m) => (
                    <a key={m} href={m} target="_blank" rel="noopener noreferrer">
                      <img src={m} alt="" loading="lazy" referrerPolicy="no-referrer" />
                    </a>
                  ))}
                </div>
              )}
              <div className="stats">
                {Object.entries(p.metrics)
                  .filter(([, v]) => v !== null && v !== "")
                  .map(([k, v]) => (
                    <span key={k}>
                      {k}: {v}
                    </span>
                  ))}
              </div>
            </article>
          ))}
        </div>
      )}
      {posts.length < total && (
        <div style={{ textAlign: "center", marginTop: 16 }}>
          <button className="btn secondary" disabled={loading} onClick={() => void loadPosts(posts.length)}>
            {loading ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
    </div>
  );
}
