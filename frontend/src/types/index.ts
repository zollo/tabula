// Mirrors the backend's Pydantic schemas (see docs/openapi.json).

export type Role = "user" | "admin";
export type Platform = "x" | "bluesky" | "reddit";
export type BackfillStatus = "pending" | "running" | "complete" | "failed";
export type PostKind = "post" | "reply" | "repost" | "quote" | "comment";
export type CredentialSource = "saved" | "environment" | "unset";

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  is_active: boolean;
  is_enabled: boolean;
  can_track: boolean;
  has_password: boolean;
  google_email: string | null;
  created_at: string;
}

export interface Token {
  access_token: string;
  token_type: string;
  user: User;
}

export interface AuthConfig {
  registration_enabled: boolean;
  google_enabled: boolean;
}

export interface GoogleAuthorization {
  authorization_url: string;
  state: string;
  code_verifier: string;
}

export interface SocialAccount {
  id: string;
  platform: Platform;
  handle: string;
  display_name: string;
  avatar_url: string | null;
  profile_url: string | null;
  scan_interval_minutes: number | null;
  last_scanned_at: string | null;
  next_scan_at: string | null;
  last_error: string | null;
  backfill_status: BackfillStatus;
  backfill_finished_at: string | null;
  post_count: number;
}

export interface Tracking {
  id: string;
  user_id: string;
  scan_interval_minutes: number;
  is_paused: boolean;
  created_at: string;
  social_account: SocialAccount;
}

export interface Post {
  id: string;
  external_id: string;
  kind: PostKind;
  url: string | null;
  text: string;
  posted_at: string;
  media: string[];
  metrics: Record<string, string | number | null>;
  fetched_at: string;
}

export interface PostPage {
  total: number;
  items: Post[];
}

export interface PlatformOption {
  value: Platform;
  label: string;
  enabled: boolean;
  configured: boolean;
}

export interface TrackingOptions {
  platforms: PlatformOption[];
  min_scan_interval_minutes: number;
  default_scan_interval_minutes: number;
  max_trackings_per_user: number;
}

export interface SystemSettings {
  allow_registration: boolean;
  enabled_platforms: Platform[];
  min_scan_interval_minutes: number;
  default_scan_interval_minutes: number;
  max_trackings_per_user: number;
  backfill_max_posts: number;
  credentials: Record<string, CredentialSource>;
  credential_values: Record<string, string>;
  platform_sources: Record<Platform, CollectionSource>;
  browser_platforms: Platform[];
  // Platforms that can be read from public pages with no login (Reddit).
  public_platforms: Platform[];
  // Platforms that can be read from a third-party archive (Reddit, via Arctic Shift).
  archive_platforms: Platform[];
  browser_page_interval_seconds: number;
  browser_daily_page_cap: number;
  public_request_interval_seconds: number;
}

// Where a platform's posts come from: the API, a signed-in browser, public pages (no login),
// or a third-party archive.
export type CollectionSource = "api" | "browser" | "public" | "archive";

export type BrowserSessionStatus = "active" | "cooling_down" | "needs_attention" | "disabled";

// A saved browser login used for browser collection (the login itself never leaves the API).
export interface BrowserSession {
  id: string;
  platform: Platform;
  label: string;
  signed_in: boolean;
  status: BrowserSessionStatus;
  last_error: string | null;
  cooldown_until: string | null;
  in_use: boolean;
  last_used_at: string | null;
  pages_today: number;
  pages_total: number;
  created_at: string;
}

export interface BuildInfo {
  version: string;
  git_commit: string;
  build_time: string;
  environment: string;
  public_url: string;
  api_docs_url: string;
  openapi_url: string;
}
