import { describe, expect, it } from "vitest";

import { parseTargetClass, targetClassBanner } from "./targetClass";

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
});