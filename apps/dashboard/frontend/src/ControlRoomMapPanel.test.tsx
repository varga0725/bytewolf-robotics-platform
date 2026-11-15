import { cleanup, render, screen, waitFor } from "@testing-library/react";
import L from "leaflet";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ControlRoomMapPanel, type ControlRoomMapWaypoint } from "./ControlRoomMapPanel";

const clickHandlers: Array<(event: { latlng: { lat: number; lng: number } }) => void> = [];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  vi.setSystemTime(new Date());
  clickHandlers.length = 0;
});

function mockTelemetry(payload: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(
    path === "/api/v1/telemetry" ? JSON.stringify(payload) : "{}",
    { status: 200 },
  ))));
}

describe("ControlRoomMapPanel", () => {
  beforeEach(() => {
    vi.mocked(L.map).mockImplementation(() => {
      const map = {
        on: vi.fn((event: string, handler: unknown) => {
          if (event === "click") clickHandlers.push(handler as (event: { latlng: { lat: number; lng: number } }) => void);
        }),
        off: vi.fn(),
        setView: vi.fn(),
        getZoom: () => 17,
        remove: vi.fn(),
      };
      return map as unknown as L.Map;
    });
    mockTelemetry({
      captured_at: new Date().toISOString(),
      heading_deg: 45,
      target_class: "sitl_verified",
      position: { latitude_deg: 37.416, longitude_deg: -122.049, absolute_altitude_m: 0, relative_altitude_m: 0 },
    });
  });

  it("renders telemetry state with live position copy", async () => {
    render(<ControlRoomMapPanel ariaLabel="Teszt térkép" />);
    expect(await screen.findByTestId("control-room-map")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/TELEMETRIA KAPCSOLÓDVA/i)).toBeInTheDocument());
    expect(screen.getByText(/SITL \(ellenőrzött\)/i)).toBeInTheDocument();
  });

  it("forwards map clicks into the plan-proposal waypoint shape", async () => {
    const picks: ControlRoomMapWaypoint[] = [];
    render(
      <ControlRoomMapPanel
        ariaLabel="Küldetési térkép"
        interactive
        onWaypointSelect={(waypoint) => picks.push(waypoint)}
      />,
    );
    await screen.findByTestId("control-room-map");
    await waitFor(() => expect(screen.getByText(/Élő pozíció/i)).toBeInTheDocument());
    await waitFor(() => expect(clickHandlers.length).toBeGreaterThan(0));

    clickHandlers.at(-1)?.({ latlng: { lat: 37.41609, lng: -122.0489 } });

    await waitFor(() => expect(picks).toHaveLength(1));
    expect(picks[0]?.north_m).toBeGreaterThan(0);
    expect(picks[0]?.east_m).toBeGreaterThan(0);
  });

  it("shows empty and stale telemetry states", async () => {
    vi.setSystemTime(new Date("2026-11-15T12:00:20.000Z"));
    mockTelemetry({
      captured_at: "2026-11-15T12:00:00.000Z",
      position: null,
      target_class: "unknown",
    });
    render(<ControlRoomMapPanel ariaLabel="Teszt térkép" />);
    expect(await screen.findByText(/nincs érvényes pozíció a mintában/i)).toBeInTheDocument();
    expect(document.querySelector(".telemetry-sample--stale")).toBeTruthy();
  });
});