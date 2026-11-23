import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import GoogleAccount from "../components/GoogleAccount";
import PendingApproval from "../components/PendingApproval";
import { useAuth } from "../context/AuthContext";
import type { User } from "../types";

export default function Account() {
  const { user, setUser } = useAuth();
  const [name, setName] = useState(user?.display_name ?? "");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  if (!user) return null;

  const save = async (body: Record<string, string>, message: string) => {
    setError(null);
    setOk(null);
    try {
      setUser(await api.patch<User>("/api/auth/me", body));
      setOk(message);
      setCurrent("");
      setNext("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Update failed");
    }
  };

  const saveName = (e: FormEvent) => {
    e.preventDefault();
    void save({ display_name: name }, "Name saved.");
  };

  const savePassword = (e: FormEvent) => {
    e.preventDefault();
    void save({ current_password: current, new_password: next }, "Password changed.");
  };

  return (
    <div className="page narrow">
      <h1>Your account</h1>
      <p className="subtitle">
        {user.email} · {user.role === "admin" ? "Admin" : "User"}
      </p>
      <PendingApproval />
      {error && <div className="alert error">{error}</div>}
      {ok && <div className="alert success">{ok}</div>}

      <form className="card" onSubmit={saveName}>
        <h2>Profile</h2>
        <div className="field">
          <label htmlFor="name">Display name</label>
          <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <button className="btn">Save</button>
      </form>

      <form className="card" onSubmit={savePassword}>
        <h2>{user.has_password ? "Change password" : "Set a password"}</h2>
        {user.has_password && (
          <div className="field">
            <label htmlFor="current">Current password</label>
            <input
              id="current"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
            />
          </div>
        )}
        <div className="field">
          <label htmlFor="new">New password</label>
          <input
            id="new"
            type="password"
            autoComplete="new-password"
            minLength={8}
            value={next}
            onChange={(e) => setNext(e.target.value)}
            required
          />
          <div className="help">At least 8 characters.</div>
        </div>
        <button className="btn">Save password</button>
      </form>

      <GoogleAccount returnTo="/account" />
    </div>
  );
}
