import { useAuth } from "../context/AuthContext";

// Shown to self-registered users until an admin enables tracking for them.
export default function PendingApproval() {
  const { user } = useAuth();
  if (!user || user.can_track) return null;
  return (
    <div className="alert info">
      <strong>Your account is waiting for approval.</strong> An administrator needs to enable
      your account before you can track social media accounts.
    </div>
  );
}
