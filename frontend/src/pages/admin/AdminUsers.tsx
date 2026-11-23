import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { formatDate } from "../../lib/format";
import type { Role, User } from "../../types";

export default function AdminUsers() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [form, setForm] = useState({
    email: "",
    display_name: "",
    password: "",
    role: "user" as Role,
  });
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const load = () => api.get<User[]>("/api/users").then(setUsers).catch(() => {});
  useEffect(() => {
    void load();
  }, []);

  const fail = (err: unknown, fallback: string) =>
    setError(err instanceof ApiError ? err.message : fallback);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setOk(null);
    try {
      const u = await api.post<User>("/api/users", { ...form, is_enabled: true });
      setOk(`Created ${u.email}. Share the password with them securely.`);
      setForm({ email: "", display_name: "", password: "", role: form.role });
      void load();
    } catch (err) {
      fail(err, "Failed to create user");
    }
  };

  const patch = async (u: User, body: Partial<User> & { password?: string }) => {
    setError(null);
    setOk(null);
    try {
      await api.patch(`/api/users/${u.id}`, body);
      void load();
    } catch (err) {
      fail(err, "Update failed");
    }
  };

  const setPassword = async (u: User) => {
    const password = prompt(`New password for ${u.email} (at least 8 characters)`);
    if (!password) return;
    await patch(u, { password });
    setOk(`Password set for ${u.email}.`);
  };

  const remove = async (u: User) => {
    if (!confirm(`Delete ${u.email}? Their trackings are removed; archived posts are kept.`)) return;
    try {
      await api.del(`/api/users/${u.id}`);
      void load();
    } catch (err) {
      fail(err, "Delete failed");
    }
  };

  const waiting = users.filter((u) => !u.can_track && u.is_active);

  return (
    <div className="page">
      <h1>Users</h1>
      <p className="subtitle">
        People who sign up can browse but can't track accounts until you enable them.
      </p>
      {error && <div className="alert error">{error}</div>}
      {ok && <div className="alert success">{ok}</div>}

      {waiting.length > 0 && (
        <div className="card">
          <h2>Waiting for approval</h2>
          <div className="list">
            {waiting.map((u) => (
              <div key={u.id} className="file-row">
                <div>
                  <div className="name">{u.display_name}</div>
                  <div className="muted small">
                    {u.email} · signed up {formatDate(u.created_at)}
                  </div>
                </div>
                <button className="btn small" onClick={() => void patch(u, { is_enabled: true })}>
                  Enable
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h2>Everyone</h2>
        <div style={{ overflowX: "auto" }}>
          <table className="simple">
            <thead>
              <tr>
                <th>Name</th>
                <th>Role</th>
                <th>Tracking</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{u.display_name}</div>
                    <div className="muted small">
                      {u.email}
                      {u.google_email ? " · Google" : ""}
                    </div>
                  </td>
                  <td>
                    <select
                      aria-label="Role"
                      value={u.role}
                      disabled={u.id === me?.id}
                      onChange={(e) => void patch(u, { role: e.target.value as Role })}
                    >
                      <option value="user">User</option>
                      <option value="admin">Admin</option>
                    </select>
                  </td>
                  <td>
                    {u.role === "admin" ? (
                      <span className="badge st-active">Always</span>
                    ) : (
                      <button
                        className={`btn small ${u.is_enabled ? "secondary" : ""}`}
                        onClick={() => void patch(u, { is_enabled: !u.is_enabled })}
                      >
                        {u.is_enabled ? "Disable" : "Enable"}
                      </button>
                    )}
                  </td>
                  <td>
                    <span className={`badge st-${u.is_active ? "active" : "disabled"}`}>
                      {u.is_active ? "Active" : "Deactivated"}
                    </span>
                  </td>
                  <td>
                    {u.id !== me?.id && (
                      <div className="row" style={{ flexWrap: "nowrap" }}>
                        <button
                          className="btn ghost small"
                          onClick={() => void patch(u, { is_active: !u.is_active })}
                        >
                          {u.is_active ? "Deactivate" : "Reactivate"}
                        </button>
                        <button className="btn ghost small" onClick={() => void setPassword(u)}>
                          Set password
                        </button>
                        <button className="btn danger small" onClick={() => void remove(u)}>
                          Delete
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <form className="card" onSubmit={create}>
        <h2>Add a user</h2>
        <div className="grid two">
          <div className="field">
            <label htmlFor="u-name">Name</label>
            <input
              id="u-name"
              type="text"
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="u-email">
              Email <span className="req">*</span>
            </label>
            <input
              id="u-email"
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="u-password">
              Password <span className="req">*</span>
            </label>
            <input
              id="u-password"
              type="password"
              minLength={8}
              autoComplete="new-password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="u-role">Role</label>
            <select
              id="u-role"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
            >
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </div>
        </div>
        <button className="btn">Create user</button>
        <p className="muted small" style={{ marginBottom: 0 }}>
          Users you add can track accounts straight away.
        </p>
      </form>
    </div>
  );
}
