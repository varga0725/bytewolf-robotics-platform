import { describe, expect, it } from "vitest";

import { effectiveTargetClassForConnection, parseTargetClass, targetClassBanner } from "./targetClass";

describe("targetClassBanner", () => {
  it("marks unknown and real targets as dangerous", () => {
    expect(targetClassBanner("unknown").showDangerBanner).toBe(true);
    expect(targetClassBanner("real_suspected").showDangerBanner).toBe(true);
    expect(targetClassBanner("sitl_verified").showDangerBanner).toBe(false);
  });

  it("parses the API allowlist verdict", () => {
    expect(parseTargetClass("sitl_verified")).toBe("sitl_verified");
    expect(parseTargetClass("bogus")).toBeNull();
  });

  it("clears verified badge when telemetry connection is stale", () => {
    expect(effectiveTargetClassForConnection("stale", "sitl_verified")).toBeNull();
    expect(effectiveTargetClassForConnection("ready", "sitl_verified")).toBe("sitl_verified");
  });
});