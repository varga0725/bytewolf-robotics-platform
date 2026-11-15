import { describe, expect, it, vi } from "vitest";

import {
  appendTrackPoint,
  chooseHomeAnchor,
  mapTelemetryConnection,
  parseMapTelemetry,
} from "./controlRoomMapTelemetry";

describe("controlRoomMapTelemetry", () => {
  it("parses heading, position and target_class from telemetry", () => {
    const sample = parseMapTelemetry({
      captured_at: "2026-11-15T10:00:00.000Z",
      heading_deg: 271.25,
      target_class: "sitl_verified",
      position: { latitude_deg: 37.416, longitude_deg: -122.049, absolute_altitude_m: 12, relative_altitude_m: 2 },
    });
    expect(sample.heading_deg).toBeCloseTo(271.25);
    expect(sample.position?.latitude_deg).toBe(37.416);
    expect(sample.target_class).toBe("sitl_verified");
  });

  it("marks stale samples and withholds track growth", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-15T10:00:20.000Z"));
    const sample = parseMapTelemetry({
      captured_at: "2026-11-15T10:00:00.000Z",
      position: { latitude_deg: 1, longitude_deg: 2, absolute_altitude_m: 1, relative_altitude_m: 1 },
    });
    const connection = mapTelemetryConnection(sample);
    expect(connection.state).toBe("stale");
    expect(appendTrackPoint([], sample, connection)).toEqual([]);
    vi.useRealTimers();
  });

  it("anchors home on the first ready fix", () => {
    const sample = parseMapTelemetry({
      captured_at: new Date().toISOString(),
      position: { latitude_deg: 37.416, longitude_deg: -122.049, absolute_altitude_m: 1, relative_altitude_m: 1 },
    });
    const connection = mapTelemetryConnection(sample);
    expect(chooseHomeAnchor(null, sample, connection)?.latitude_deg).toBe(37.416);
  });
});