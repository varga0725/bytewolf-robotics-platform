import { describe, expect, it } from "vitest";

import { buildDemoWaypointMissionPayload } from "./missionWaypointPlan";

const home = {
  latitude_deg: 37.416,
  longitude_deg: -122.049,
  absolute_altitude_m: 0,
  captured_at: "2026-11-15T10:00:00.000Z",
};

describe("buildDemoWaypointMissionPayload", () => {
  it("serializes geographic waypoints and home for the demo-waypoint propose endpoint", () => {
    const payload = buildDemoWaypointMissionPayload(
      [
        { latitude_deg: 37.41609, longitude_deg: -122.0489, relative_altitude_m: 8 },
        { latitude_deg: 37.41619, longitude_deg: -122.0487, relative_altitude_m: 8 },
      ],
      8,
      "  Többpontos teszt  ",
      home,
    );

    expect(payload).toEqual({
      takeoff_altitude_m: 8,
      goal: "Többpontos teszt",
      home,
      waypoints: [
        { latitude_deg: 37.41609, longitude_deg: -122.0489, relative_altitude_m: 8 },
        { latitude_deg: 37.41619, longitude_deg: -122.0487, relative_altitude_m: 8 },
      ],
    });
  });
});