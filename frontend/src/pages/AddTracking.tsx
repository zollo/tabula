import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import PendingApproval from "../components/PendingApproval";
import { useAuth } from "../context/AuthContext";
import { formatInterval, INTERVAL_CHOICES } from "../lib/format";
import type { Platform, Tracking, TrackingOptions } from "../types";

const PLACEHOLDERS: Record<Platform, string> = {
  x: "@handle or https://x.com/handle",
  bluesky: "name.bsky.social",
  reddit: "u/username",
};

export default function AddTracking() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [options, setOptions] = useState<TrackingOptions | null>(null);
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [handle, setHandle] = useState("");
  const [interval, setInterval] = useState<number>(60);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<TrackingOptions>("/api/options")
      .then((o) => {
        setOptions(o);
        setInterval(o.default_scan_interval_minutes);
        setPlatform(o.platforms.find((p) => p.enabled)?.value ?? null);
      })
      .catch(() => setError("Could not load the tracking options"));
  }, []);

  const choices = options
    ? Array.from(
        new Set([...INTERVAL_CHOICES, options.default_scan_interval_minutes]),
      )
        .filter((m) => m >= options.min_scan_interval_minutes)
        .sort((a, b) => a - b)
    : [];
  const selected = options?.platforms.find((p) => p.value === platform);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!platform) return;
    setError(null);
    setBusy(true);
    try {
      const t = await api.post<Tracking>("/api/trackings", {
        platform,
        handle,
        scan_interval_minutes: interval,
      });
      navigate(`/accounts/${t.social_account.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add the account");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page narrow">
      <h1>Track an account</h1>
      <p className="subtitle">
        Tabula archives the account's history right away, then checks for new posts on your
        schedule.
      </p>
      <PendingApproval />
      {error && <div className="alert error">{error}</div>}
      {options && (
        <form className="card" onSubmit={submit}>
          <div className="field">
            <label>Platform</label>
            <div className="filters" style={{ margin: 0 }}>
              {options.platforms
                .filter((p) => p.enabled)
                .map((p) => (
                  <button
                    type="button"
                    key={p.value}
                    className={`pill${platform === p.value ? " active" : ""}`}
                    onClick={() => setPlatform(p.value)}
                  >
                    {p.label}
                  </button>
                ))}
            </div>
            {selected && !selected.configured && (
              <div className="help">
                {selected.label} isn't connected yet; an admin needs to add its API credentials
                in Settings.
              </div>
            )}
          </div>
          <div className="field">
            <label htmlFor="handle">Account</label>
            <input
              id="handle"
              type="text"
              autoCapitalize="none"
              autoCorrect="off"
              placeholder={platform ? PLACEHOLDERS[platform] : ""}
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              required
            />
            <div className="help">A handle or a link to their profile.</div>
          </div>
          <div className="field">
            <label htmlFor="interval">Check for new posts</label>
            <select
              id="interval"
              value={interval}
              onChange={(e) => setInterval(Number(e.target.value))}
            >
              {choices.map((m) => (
                <option key={m} value={m}>
                  {formatInterval(m)}
                </option>
              ))}
            </select>
          </div>
          <button className="btn block" disabled={busy || !platform || !user?.can_track}>
            {busy ? "Looking them up…" : "Start tracking"}
          </button>
        </form>
      )}
    </div>
  );
}
