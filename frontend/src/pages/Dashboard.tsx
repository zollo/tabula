import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import Avatar from "../components/Avatar";
import PendingApproval from "../components/PendingApproval";
import { useAuth } from "../context/AuthContext";
import {
  formatDateTime,
  formatInterval,
  handleLabel,
  PLATFORM_LABELS,
  trackingStatus,
} from "../lib/format";
import type { Platform, Tracking } from "../types";

type Filter = "all" | Platform;

export default function Dashboard() {
  const { user, isAdmin } = useAuth();
  const [trackings, setTrackings] = useState<Tracking[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [everyone, setEveryone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTrackings(null);
    api
      .get<Tracking[]>(`/api/trackings${everyone ? "?all_users=true" : ""}`)
      .then(setTrackings)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load"));
  }, [everyone]);

  const shown = (trackings ?? []).filter(
    (t) => filter === "all" || t.social_account.platform === filter,
  );

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Tracked accounts</h1>
          <p className="subtitle" style={{ marginBottom: 0 }}>
            Everything Tabula is archiving for {everyone ? "everyone" : "you"}.
          </p>
        </div>
        {user?.can_track && (
          <Link to="/track" className="btn">
            Track an account
          </Link>
        )}
      </div>

      <PendingApproval />
      {error && <div className="alert error">{error}</div>}

      <div className="filters">
        {(["all", "x", "bluesky", "reddit"] as Filter[]).map((f) => (
          <button
            key={f}
            className={`pill${filter === f ? " active" : ""}`}
            onClick={() => setFilter(f)}
          >
            {f === "all" ? "All" : PLATFORM_LABELS[f]}
          </button>
        ))}
        {isAdmin && (
          <button
            className={`pill${everyone ? " active" : ""}`}
            onClick={() => setEveryone(!everyone)}
          >
            All users
          </button>
        )}
      </div>

      {trackings === null ? (
        <p className="muted">Loading…</p>
      ) : shown.length === 0 ? (
        <div className="empty">
          {trackings.length === 0
            ? "You're not tracking anyone yet."
            : "No tracked accounts on this platform."}
        </div>
      ) : (
        <div className="list">
          {shown.map((t) => {
            const a = t.social_account;
            const status = trackingStatus(t);
            return (
              <Link key={t.id} to={`/accounts/${a.id}`} className="item">
                <div className="top">
                  <div className="account-row">
                    <Avatar account={a} />
                    <div className="who">
                      <div className="title">{a.display_name || a.handle}</div>
                      <div className="company">
                        <span className="platform">{PLATFORM_LABELS[a.platform]}</span>{" "}
                        {handleLabel(a.platform, a.handle)}
                      </div>
                    </div>
                  </div>
                  <span className={`badge st-${status.key}`}>{status.label}</span>
                </div>
                <div className="meta">
                  <span>{a.post_count.toLocaleString()} posts</span>
                  <span>{formatInterval(t.scan_interval_minutes)}</span>
                  <span>Last scan {formatDateTime(a.last_scanned_at)}</span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
