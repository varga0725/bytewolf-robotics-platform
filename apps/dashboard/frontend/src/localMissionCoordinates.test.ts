import { describe, expect, it } from "vitest";

import { globalOffsetMeters, pointFromLocalOffset } from "./localMissionCoordinates";

describe("localMissionCoordinates", () => {
  const home = { latitude_deg: 37.416, longitude_deg: -122.049 };

  it("round-trips a local north/east offset through WGS84", () => {
    const offset = { north_m: 10, east_m: -5 };
    const point = pointFromLocalOffset(home, offset);
    const read = globalOffsetMeters(home, point);
    expect(read.north_m).toBeCloseTo(offset.north_m, 5);
    expect(read.east_m).toBeCloseTo(offset.east_m, 5);
  });
});