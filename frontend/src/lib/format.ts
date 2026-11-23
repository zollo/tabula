import type { BackfillStatus, Platform, PostKind, Tracking } from "../types";

export const PLATFORM_LABELS: Record<Platform, string> = {
  x: "X",
  bluesky: "Bluesky",
  reddit: "Reddit",
};

export const KIND_LABELS: Record<PostKind, string> = {
  post: "Post",
  reply: "Reply",
  repost: "Repost",
  quote: "Quote",
  comment: "Comment",
};

export const BACKFILL_LABELS: Record<BackfillStatus, string> = {
  pending: "Backfill queued",
  running: "Backfilling history",
  complete: "History archived",
  failed: "Backfill failed",
};

// Frequencies offered in the UI; anything at or above the system minimum works via the API.
export const INTERVAL_CHOICES = [15, 30, 60, 180, 360, 720, 1440, 10080];

export function formatInterval(minutes: number | null | undefined): string {
  if (!minutes) return "Not scanned";
  if (minutes % 10080 === 0) return minutes === 10080 ? "Weekly" : `Every ${minutes / 10080} weeks`;
  if (minutes % 1440 === 0) return minutes === 1440 ? "Daily" : `Every ${minutes / 1440} days`;
  if (minutes % 60 === 0) return minutes === 60 ? "Hourly" : `Every ${minutes / 60} hours`;
  return `Every ${minutes} min`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function handleLabel(platform: Platform, handle: string): string {
  return platform === "reddit" ? `u/${handle}` : `@${handle}`;
}

// One status for a tracked account, most important first.
export function trackingStatus(t: Tracking): { key: string; label: string } {
  const a = t.social_account;
  if (t.is_paused) return { key: "paused", label: "Paused" };
  if (a.backfill_status === "failed") return { key: "failed", label: BACKFILL_LABELS.failed };
  if (a.last_error) return { key: "error", label: "Last scan failed" };
  if (a.backfill_status !== "complete")
    return { key: a.backfill_status, label: BACKFILL_LABELS[a.backfill_status] };
  return { key: "active", label: "Tracking" };
}
