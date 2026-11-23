import { describe, expect, it } from "vitest";
import { formatInterval, handleLabel, trackingStatus } from "./format";
import type { Tracking } from "../types";

const tracking = (overrides: Partial<Tracking["social_account"]> = {}, paused = false): Tracking => ({
  id: "t",
  user_id: "u",
  scan_interval_minutes: 60,
  is_paused: paused,
  created_at: "2026-01-01T00:00:00Z",
  social_account: {
    id: "a",
    platform: "bluesky",
    handle: "alice",
    display_name: "Alice",
    avatar_url: null,
    profile_url: null,
    scan_interval_minutes: 60,
    last_scanned_at: null,
    next_scan_at: null,
    last_error: null,
    backfill_status: "complete",
    backfill_finished_at: null,
    post_count: 0,
    ...overrides,
  },
});

describe("formatInterval", () => {
  it("names common intervals", () => {
    expect(formatInterval(60)).toBe("Hourly");
    expect(formatInterval(1440)).toBe("Daily");
    expect(formatInterval(10080)).toBe("Weekly");
    expect(formatInterval(360)).toBe("Every 6 hours");
    expect(formatInterval(15)).toBe("Every 15 min");
    expect(formatInterval(null)).toBe("Not scanned");
  });
});

describe("handleLabel", () => {
  it("uses each platform's convention", () => {
    expect(handleLabel("reddit", "spez")).toBe("u/spez");
    expect(handleLabel("x", "jack")).toBe("@jack");
  });
});

describe("trackingStatus", () => {
  it("prefers paused, then failures, then backfill progress", () => {
    expect(trackingStatus(tracking({}, true)).key).toBe("paused");
    expect(trackingStatus(tracking({ backfill_status: "failed" })).key).toBe("failed");
    expect(trackingStatus(tracking({ last_error: "boom" })).key).toBe("error");
    expect(trackingStatus(tracking({ backfill_status: "running" })).key).toBe("running");
    expect(trackingStatus(tracking()).key).toBe("active");
  });
});
