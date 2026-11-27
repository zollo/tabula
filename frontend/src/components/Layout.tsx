import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import Logo from "./Logo";
import ThemeToggle from "./ThemeToggle";

interface Tab {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
}

const USER_TABS: Tab[] = [
  { to: "/", label: "Tracked", icon: "🗂️", end: true },
  { to: "/track", label: "Track", icon: "➕" },
  { to: "/account", label: "Account", icon: "👤" },
  { to: "/info", label: "About", icon: "ℹ️" },
];

const ADMIN_TABS: Tab[] = [
  { to: "/", label: "Tracked", icon: "🗂️", end: true },
  { to: "/track", label: "Track", icon: "➕" },
  { to: "/admin/users", label: "Users", icon: "👥" },
  { to: "/admin/settings", label: "Settings", icon: "⚙️" },
  { to: "/account", label: "Account", icon: "👤" },
];

export default function Layout() {
  const { user, isAdmin, logout } = useAuth();
  const navigate = useNavigate();
  const tabs = isAdmin ? ADMIN_TABS : USER_TABS;

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <>
      <nav className="app-nav">
        <NavLink to="/" className="brand">
          <Logo />
          Tabula
        </NavLink>
        <div className="links">
          {tabs.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.end}>
              {t.label}
            </NavLink>
          ))}
        </div>
        <div className="spacer" />
        <span className="user">{user?.display_name || user?.email}</span>
        <ThemeToggle />
        <button className="btn secondary small" onClick={handleLogout}>
          Sign out
        </button>
      </nav>
      <Outlet />
      <nav className="tabbar" aria-label="Main">
        {tabs.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}>
            <span className="ico" aria-hidden>
              {t.icon}
            </span>
            {t.label}
          </NavLink>
        ))}
      </nav>
    </>
  );
}
