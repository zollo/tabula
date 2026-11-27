import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../../api/client";
import { formatDateTime, PLATFORM_LABELS } from "../../lib/format";
import type {
  BrowserSession,
  BrowserSessionStatus,
  CollectionSource,
  Platform,
  SystemSettings,
} from "../../types";

const STATUS: Record<BrowserSessionStatus, { label: string; css: string }> = {
  active: { label: "Ready", css: "st-active" },
  cooling_down: { label: "Cooling down", css: "st-pending" },
  needs_attention: { label: "Needs a new login", css: "st-failed" },
  disabled: { label: "Off", css: "st-disabled" },
};

interface Props {
  settings: SystemSettings;
  // Saves settings changes; resolves once saved.
  onSave: (body: Partial<SystemSettings>, message: string) => Promise<void>;
}

/** Admin card: choose API, browser, public pages or archive per platform, pacing, and saved logins. */
export default function BrowserCollection({ settings, onSave }: Props) {
  const [sessions, setSessions] = useState<BrowserSession[]>([]);
  const [sources, setSources] = useState(settings.platform_sources);
  const [pace, setPace] = useState(settings.browser_page_interval_seconds);
  const [cap, setCap] = useState(settings.browser_daily_page_cap);
  const [publicPace, setPublicPace] = useState(settings.public_request_interval_seconds);
  const [platform, setPlatform] = useState<Platform>(settings.browser_platforms[0] ?? "x");
  const [label, setLabel] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<BrowserSession[]>("/api/browser-sessions").then(setSessions).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    }
  };

  const saveSources = (e: FormEvent) => {
    e.preventDefault();
    void onSave(
      {
        platform_sources: sources,
        browser_page_interval_seconds: pace,
        browser_daily_page_cap: cap,
        public_request_interval_seconds: publicPace,
      },
      "Collection settings saved.",
    );
  };

  const add = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const storage_state = file ? await file.text() : null;
      await api.post("/api/browser-sessions", { platform, label, storage_state });
      setLabel("");
      setFile(null);
      setFileKey((k) => k + 1);
    });
  };

  const replace = (s: BrowserSession, f: File | undefined) => {
    if (!f) return;
    void run(async () =>
      api.patch(`/api/browser-sessions/${s.id}`, { storage_state: await f.text() }),
    );
  };

  const remove = (s: BrowserSession) => {
    if (!confirm(`Delete the ${PLATFORM_LABELS[s.platform]} session "${s.label}"?`)) return;
    void run(() => api.del(`/api/browser-sessions/${s.id}`));
  };

  const needsLogin = platform === "x";
  const sourcePlatforms = [
    ...new Set([
      ...settings.browser_platforms,
      ...settings.public_platforms,
      ...settings.archive_platforms,
    ]),
  ];

  return (
    <div className="card">
      <h2>Collection sources</h2>
      <p className="muted small" style={{ marginTop: -6 }}>
        Instead of the API, X and Reddit can be read with a headless browser signed in with
        accounts you own. This is against both sites' terms, and they may lock or suspend those
        accounts. Tabula paces each login and stops using one the moment it's challenged.
      </p>
      <p className="muted small">
        Reddit can also be read with no login, app or credentials at all, from the Arctic Shift
        archive. It ingests Reddit within minutes and keeps whole histories (no 1,000-item limit),
        but it's a free community service with no uptime guarantee, and scores are as of when it
        collected each post. Reddit's own public pages are a further option, but Reddit has refused
        signed-out requests since mid-2026.
      </p>
      {error && <div className="alert error">{error}</div>}

      <form onSubmit={saveSources}>
        <div className="grid two">
          {sourcePlatforms.map((p) => (
            <div className="field" key={p}>
              <label htmlFor={`src-${p}`}>{PLATFORM_LABELS[p]} posts come from</label>
              <select
                id={`src-${p}`}
                value={sources[p] ?? "api"}
                onChange={(e) =>
                  setSources({ ...sources, [p]: e.target.value as CollectionSource })
                }
              >
                <option value="api">The API</option>
                {settings.browser_platforms.includes(p) && (
                  <option value="browser">A browser session</option>
                )}
                {settings.public_platforms.includes(p) && (
                  <option value="public">Public pages (no login)</option>
                )}
                {settings.archive_platforms.includes(p) && (
                  <option value="archive">Arctic Shift archive (no login)</option>
                )}
              </select>
            </div>
          ))}
          {settings.public_platforms.length > 0 && (
            <div className="field">
              <label htmlFor="public-pace">Seconds between public requests</label>
              <input
                id="public-pace"
                type="number"
                min={6}
                value={publicPace}
                onChange={(e) => setPublicPace(Number(e.target.value))}
              />
              <div className="help">Shared by all workers. 6 or more.</div>
            </div>
          )}
          <div className="field">
            <label htmlFor="pace">Seconds between pages, per login</label>
            <input
              id="pace"
              type="number"
              min={5}
              value={pace}
              onChange={(e) => setPace(Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label htmlFor="cap">Pages per login per day</label>
            <input
              id="cap"
              type="number"
              min={0}
              value={cap}
              onChange={(e) => setCap(Number(e.target.value))}
            />
            <div className="help">0 means no daily cap.</div>
          </div>
        </div>
        <button className="btn">Save collection settings</button>
      </form>

      <h3 style={{ marginTop: 24 }}>Saved logins</h3>
      {sessions.length === 0 ? (
        <p className="muted small">No saved logins yet.</p>
      ) : (
        <div className="list">
          {sessions.map((s) => (
            <div key={s.id} className="file-row session-row">
              <div className="info">
                <div>
                  <span className="name">{s.label}</span>{" "}
                  <span className="muted small">
                    {PLATFORM_LABELS[s.platform]}
                    {s.signed_in ? "" : " (signed out)"}
                  </span>{" "}
                  <span className={`badge ${STATUS[s.status].css}`}>
                    {s.in_use ? "In use" : STATUS[s.status].label}
                  </span>
                </div>
                <div className="muted small">
                  {s.pages_today} pages today, {s.pages_total} in total
                  {s.last_used_at ? `, last used ${formatDateTime(s.last_used_at)}` : ""}
                  {s.status === "cooling_down" && s.cooldown_until
                    ? `, resumes ${formatDateTime(s.cooldown_until)}`
                    : ""}
                </div>
                {s.last_error && <div className="small error-text">{s.last_error}</div>}
              </div>
              <div className="row actions">
                <ReplaceLogin onFile={(f) => replace(s, f)} />
                <button
                  type="button"
                  className="btn ghost small"
                  onClick={() =>
                    void run(() =>
                      api.patch(`/api/browser-sessions/${s.id}`, {
                        enabled: s.status === "disabled" || s.status === "needs_attention",
                      }),
                    )
                  }
                >
                  {s.status === "disabled" || s.status === "needs_attention" ? "Turn on" : "Turn off"}
                </button>
                <button type="button" className="btn ghost small" onClick={() => remove(s)}>
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <form onSubmit={add} style={{ marginTop: 16 }}>
        <h3>Add a login</h3>
        <p className="muted small" style={{ marginTop: 0 }}>
          On your own computer, run <code>python capture_session.py x x-session.json</code> (from{" "}
          <code>backend/app/tools/</code>), sign in in the window that opens, and upload the file
          here. Treat it like a password.
          {!needsLogin && " A Reddit session can also be signed out: leave the file empty."}
        </p>
        <div className="grid two">
          <div className="field">
            <label htmlFor="sess-platform">Platform</label>
            <select
              id="sess-platform"
              value={platform}
              onChange={(e) => setPlatform(e.target.value as Platform)}
            >
              {settings.browser_platforms.map((p) => (
                <option key={p} value={p}>
                  {PLATFORM_LABELS[p]}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="sess-label">Name</label>
            <input
              id="sess-label"
              type="text"
              required
              placeholder="e.g. archive account 1"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </div>
        </div>
        <div className="field">
          <label htmlFor="sess-file">Session file</label>
          <input
            key={fileKey}
            id="sess-file"
            type="file"
            accept="application/json,.json"
            required={needsLogin}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <button className="btn">Add login</button>
      </form>
    </div>
  );
}

/** A real button (keyboard-reachable) that opens a file picker for a new login. */
function ReplaceLogin({ onFile }: { onFile: (f: File | undefined) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" className="btn ghost small" onClick={() => input.current?.click()}>
        Replace login
      </button>
      <input
        ref={input}
        type="file"
        accept="application/json,.json"
        hidden
        tabIndex={-1}
        onChange={(e) => {
          onFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </>
  );
}
