import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../../api/client";
import { formatInterval, PLATFORM_LABELS } from "../../lib/format";
import type { CredentialSource, Platform, SystemSettings } from "../../types";
import BrowserCollection from "./BrowserCollection";

// PATCH body: credentials are new values (or null to clear), unlike the read shape.
type SettingsUpdate = Partial<
  Omit<SystemSettings, "credentials" | "credential_values" | "browser_platforms">
> & {
  credentials?: Record<string, string | null>;
};

const PLATFORMS: Platform[] = ["x", "bluesky", "reddit"];

interface CredentialField {
  key: string;
  label: string;
  secret: boolean;
  help?: string;
}

const CREDENTIALS: { platform: Platform; fields: CredentialField[]; note: string }[] = [
  {
    platform: "x",
    note: "Reading timelines needs a paid X API tier. Only the 3,200 most recent posts are reachable.",
    fields: [{ key: "x_bearer_token", label: "Bearer token", secret: true }],
  },
  {
    platform: "bluesky",
    note: "Uses Bluesky's public AppView; no credentials needed.",
    fields: [{ key: "bluesky_appview_url", label: "AppView URL", secret: false }],
  },
  {
    platform: "reddit",
    note: "Create a \"script\" app at reddit.com/prefs/apps. Reddit only lists about 1,000 items per user.",
    fields: [
      { key: "reddit_client_id", label: "Client ID", secret: false },
      { key: "reddit_client_secret", label: "Client secret", secret: true },
      { key: "reddit_user_agent", label: "User agent", secret: false },
    ],
  },
];

const SOURCE_LABELS: Record<CredentialSource, string> = {
  saved: "Saved here",
  environment: "From environment",
  unset: "Not set",
};

export default function AdminSettings() {
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  useEffect(() => {
    api.get<SystemSettings>("/api/settings").then(setSettings).catch(() => {});
  }, []);

  if (!settings) return <div className="page narrow muted">Loading…</div>;

  const save = async (body: SettingsUpdate, message: string) => {
    setError(null);
    setOk(null);
    try {
      setSettings(await api.patch<SystemSettings>("/api/settings", body));
      setOk(message);
      setCreds({});
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed");
    }
  };

  const saveGeneral = (e: FormEvent) => {
    e.preventDefault();
    void save(
      {
        allow_registration: settings.allow_registration,
        enabled_platforms: settings.enabled_platforms,
        min_scan_interval_minutes: settings.min_scan_interval_minutes,
        default_scan_interval_minutes: settings.default_scan_interval_minutes,
        max_trackings_per_user: settings.max_trackings_per_user,
        backfill_max_posts: settings.backfill_max_posts,
      },
      "Settings saved.",
    );
  };

  const saveCreds = (e: FormEvent) => {
    e.preventDefault();
    const changed = Object.fromEntries(Object.entries(creds).filter(([, v]) => v.trim() !== ""));
    if (Object.keys(changed).length === 0) return;
    void save({ credentials: changed }, "Credentials saved.");
  };

  const clearCred = (key: string) =>
    void save({ credentials: { [key]: null } }, "Saved value cleared; the environment value applies.");

  const togglePlatform = (p: Platform) => {
    const on = settings.enabled_platforms.includes(p);
    setSettings({
      ...settings,
      enabled_platforms: on
        ? settings.enabled_platforms.filter((x) => x !== p)
        : [...settings.enabled_platforms, p],
    });
  };

  const num = (key: keyof SystemSettings) => ({
    value: settings[key] as number,
    onChange: (e: { target: { value: string } }) =>
      setSettings({ ...settings, [key]: Number(e.target.value) }),
  });

  return (
    <div className="page narrow">
      <h1>System settings</h1>
      <p className="subtitle">Sign-up, scanning limits and platform connections.</p>
      {error && <div className="alert error">{error}</div>}
      {ok && <div className="alert success">{ok}</div>}

      <form className="card" onSubmit={saveGeneral}>
        <h2>General</h2>
        <div className="field">
          <label className="check">
            <input
              type="checkbox"
              checked={settings.allow_registration}
              onChange={(e) => setSettings({ ...settings, allow_registration: e.target.checked })}
            />
            Let people sign up (they still need you to enable them)
          </label>
        </div>
        <div className="field">
          <label>Platforms users can track</label>
          {PLATFORMS.map((p) => (
            <label key={p} className="check">
              <input
                type="checkbox"
                checked={settings.enabled_platforms.includes(p)}
                onChange={() => togglePlatform(p)}
              />
              {PLATFORM_LABELS[p]}
            </label>
          ))}
        </div>
        <div className="grid two">
          <div className="field">
            <label htmlFor="min">Shortest scan interval (minutes)</label>
            <input id="min" type="number" min={1} {...num("min_scan_interval_minutes")} />
            <div className="help">{formatInterval(settings.min_scan_interval_minutes)}</div>
          </div>
          <div className="field">
            <label htmlFor="default">Default scan interval (minutes)</label>
            <input id="default" type="number" min={1} {...num("default_scan_interval_minutes")} />
            <div className="help">{formatInterval(settings.default_scan_interval_minutes)}</div>
          </div>
          <div className="field">
            <label htmlFor="max">Accounts per user</label>
            <input id="max" type="number" min={0} {...num("max_trackings_per_user")} />
            <div className="help">0 means unlimited. Admins are never limited.</div>
          </div>
          <div className="field">
            <label htmlFor="backfill">Backfill up to (posts)</label>
            <input id="backfill" type="number" min={0} {...num("backfill_max_posts")} />
            <div className="help">0 means as far back as the platform allows.</div>
          </div>
        </div>
        <button className="btn">Save settings</button>
      </form>

      <form className="card" onSubmit={saveCreds}>
        <h2>Platform connections</h2>
        <p className="muted small" style={{ marginTop: -6 }}>
          Values saved here are encrypted and override the environment variables. Secrets are
          never shown again.
        </p>
        {CREDENTIALS.map(({ platform, fields, note }) => (
          <div key={platform} style={{ marginBottom: 18 }}>
            <h3>{PLATFORM_LABELS[platform]}</h3>
            <p className="muted small" style={{ marginTop: 0 }}>
              {note}
            </p>
            {fields.map((f) => {
              const source = settings.credentials[f.key] ?? "unset";
              return (
                <div className="field" key={f.key}>
                  <label htmlFor={f.key}>
                    {f.label}{" "}
                    <span className={`badge st-${source === "unset" ? "paused" : "active"}`}>
                      {SOURCE_LABELS[source]}
                    </span>
                  </label>
                  <div className="row" style={{ flexWrap: "nowrap" }}>
                    <input
                      id={f.key}
                      type={f.secret ? "password" : "text"}
                      autoComplete="off"
                      placeholder={
                        f.secret
                          ? source === "unset"
                            ? "Not set"
                            : "•••••••• (enter a new value to replace)"
                          : settings.credential_values[f.key] ?? ""
                      }
                      value={creds[f.key] ?? ""}
                      onChange={(e) => setCreds({ ...creds, [f.key]: e.target.value })}
                    />
                    {source === "saved" && (
                      <button type="button" className="btn ghost small" onClick={() => clearCred(f.key)}>
                        Clear
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ))}
        <button className="btn">Save connections</button>
      </form>

      <BrowserCollection settings={settings} onSave={save} />
    </div>
  );
}
