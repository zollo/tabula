import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Account from "./pages/Account";
import AccountView from "./pages/AccountView";
import AddTracking from "./pages/AddTracking";
import Dashboard from "./pages/Dashboard";
import GoogleCallback from "./pages/GoogleCallback";
import Info from "./pages/Info";
import Login from "./pages/Login";
import Register from "./pages/Register";
import AdminSettings from "./pages/admin/AdminSettings";
import AdminUsers from "./pages/admin/AdminUsers";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/auth/google/callback" element={<GoogleCallback />} />

      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<Dashboard />} />
        <Route path="/track" element={<AddTracking />} />
        <Route path="/accounts/:id" element={<AccountView />} />
        <Route path="/account" element={<Account />} />
        <Route path="/info" element={<Info />} />
      </Route>

      <Route
        element={
          <ProtectedRoute adminOnly>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route path="/admin/users" element={<AdminUsers />} />
        <Route path="/admin/settings" element={<AdminSettings />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
