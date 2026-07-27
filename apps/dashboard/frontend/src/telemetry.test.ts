import { describe, expect, it } from "vitest";

import { formatTelemetry, telemetryConnection } from "./telemetry";

describe("formatTelemetry", () => {
  it("formats the values that are safe to display", () => {
    expect(
      formatTelemetry({
        position: {
          latitude_deg: 47.4979,
          longitude_deg: 19.0402,
          absolute_altitude_m: 125.5,
          relative_altitude_m: 12.3,
        },
        battery_percent: 78.5,
        in_air: true,
        captured_at: "2026-07-25T12:00:00Z",
      }),
    ).toEqual({
      flight: "LEVEGŐBEN",
      altitude: "12,3 m",
      battery: "78,5 %",
      position: "47,497900, 19,040200",
    });
  });

  it("never invents a missing telemetry value", () => {
    expect(
      formatTelemetry({
        position: null,
        battery_percent: null,
        in_air: null,
        captured_at: null,
      }),
    ).toEqual({
      flight: "NEM ELÉRHETŐ",
      altitude: "NEM ELÉRHETŐ",
      battery: "NEM ELÉRHETŐ",
      position: "NEM ELÉRHETŐ",
    });
  });
});

describe("telemetryConnection", () => {
  it("does not present an old snapshot as connected", () => {
    expect(telemetryConnection("2026-07-25T11:59:49Z", new Date("2026-07-25T12:00:00Z"))).toEqual({
      state: "stale",
      label: "TELEMETRIA ELAVULT",
    });
  });

  it("rejects a missing or invalid capture timestamp", () => {
    expect(telemetryConnection(null, new Date("2026-07-25T12:00:00Z"))).toEqual({
      state: "unavailable",
      label: "TELEMETRIA NEM ELÉRHETŐ",
    });
    expect(telemetryConnection("not-a-time", new Date("2026-07-25T12:00:00Z"))).toEqual({
      state: "unavailable",
      label: "TELEMETRIA NEM ELÉRHETŐ",
    });
  });
});
