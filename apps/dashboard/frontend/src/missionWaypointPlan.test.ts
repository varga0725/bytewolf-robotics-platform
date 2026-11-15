import { describe, expect, it } from "vitest";

import { buildDemoWaypointMissionPayload } from "./missionWaypointPlan";

describe("buildDemoWaypointMissionPayload", () => {
  it("serializes ordered map waypoints for the demo-waypoint propose endpoint", () => {
    const payload = buildDemoWaypointMissionPayload(
      [
        { north_m: 5, east_m: 0, relative_altitude_m: 8 },
        { north_m: 5, east_m: 5, relative_altitude_m: 8 },
      ],
      8,
      "  Többpontos teszt  ",
    );

    expect(payload).toEqual({
      takeoff_altitude_m: 8,
      goal: "Többpontos teszt",
      waypoints: [
        { north_m: 5, east_m: 0, relative_altitude_m: 8 },
        { north_m: 5, east_m: 5, relative_altitude_m: 8 },
      ],
    });
  });
});