import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import L from "leaflet";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MissionPage } from "./MissionPage";

const mapClickHandlers: Array<(event: { latlng: { lat: number; lng: number } }) => void> = [];

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

const capturedAt = new Date().toISOString();

const mockTelemetry = {
  captured_at: capturedAt,
  heading_deg: 12,
  position: { latitude_deg: 37.416, longitude_deg: -122.049, absolute_altitude_m: 0, relative_altitude_m: 0 },
  home: {
    latitude_deg: 37.416,
    longitude_deg: -122.049,
    absolute_altitude_m: 0,
    captured_at: capturedAt,
  },
};

function missionPayload(path: string, overrides: Record<string, unknown> = {}) {
  if (path === "/api/v1/telemetry") return mockTelemetry;
  if (path === "/api/v1/safety-envelope") {
    return overrides.safety ?? { max_altitude_m: 20, max_radius_m: 50, minimum_battery_percent_to_start: 35, geofence_vertices_m: [] };
  }
  if (path === "/api/v1/world-map") {
    return overrides.world ?? { cells: [] };
  }
  if (path === "/api/v1/map-view/meta") return {};
  return overrides.default ?? {};
}

describe("MissionPage", () => {
  beforeEach(() => {
    mapClickHandlers.length = 0;
    vi.mocked(L.map).mockImplementation(() => {
      const instance = {
        on: vi.fn((event: string, handler: unknown) => {
          if (event === "click") mapClickHandlers.push(handler as (event: { latlng: { lat: number; lng: number } }) => void);
        }),
        off: vi.fn(),
        setView: vi.fn(),
        getZoom: () => 17,
        remove: vi.fn(),
      };
      return instance as unknown as L.Map;
    });
  });
  it("resumes monitoring after a reload and retries a transient status failure", async () => {
    const planId = "e006e612-bc67-4ae2-927f-dc0e88f174e9.demo-waypoint-mission.json";
    window.sessionStorage.setItem("bytewolfActivePlan", planId);
    const statusPath = `/api/v1/plans/${encodeURIComponent(planId)}/status`;
    let statusRequests = 0;
    const fetchMock = vi.fn(async (path: string) => {
      if (path !== statusPath) return new Response(JSON.stringify(missionPayload(path)), { status: 200 });
      statusRequests += 1;
      if (statusRequests === 1) return new Response(null, { status: 503 });
      return new Response(JSON.stringify({ status: "completed", message: "Küldetés befejezve." }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage commandsEnabled />);

    expect(await screen.findByText(`Futó küldetés követése folytatódik: ${planId}`)).toBeInTheDocument();
    expect(await screen.findByText(/átmenetileg nem elérhető/, {}, { timeout: 2000 })).toBeInTheDocument();
    expect(window.sessionStorage.getItem("bytewolfActivePlan")).toBe(planId);
    expect(await screen.findByText("Küldetés befejezve.", {}, { timeout: 2000 })).toBeInTheDocument();
    expect(statusRequests).toBe(2);
    expect(window.sessionStorage.getItem("bytewolfActivePlan")).toBeNull();
  });

  it("hides mission approval controls in ground control room mode", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(missionPayload(path)), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage commandsEnabled={false} />);
    await screen.findByText(/Ground Control Room mód/);
    expect(screen.getByRole("application", { name: "Küldetési OpenStreetMap térkép" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Terv ellenőrzése" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Kifejezett jóváhagyás/ })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(expect.stringContaining("/api/v1/missions/"), expect.anything());
  });

  it("submits ordered map waypoints to the demo-waypoint propose endpoint", async () => {
    let waypointPosts = 0;
    const fetchMock = vi.fn((path: string, init?: RequestInit) => {
      if (path === "/api/v1/missions/demo-waypoint-mission") {
        waypointPosts += 1;
        return Promise.resolve(new Response(JSON.stringify({
          plan_id: "plan-wp",
          summary: "Többpontos",
          goal: "Útvonal",
          steps: ["TAKEOFF", "GOTO_1", "GOTO_2", "RTL", "LAND"],
          waypoints: [],
        }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify(missionPayload(path)), { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage commandsEnabled />);
    await screen.findByText(/Aktív korlát/);
    fireEvent.click(screen.getByRole("radio", { name: "Többpontos útvonal" }));
    await waitFor(() => expect(mapClickHandlers.length).toBeGreaterThan(0));
    mapClickHandlers.at(-1)?.({ latlng: { lat: 37.41609, lng: -122.0489 } });
    mapClickHandlers.at(-1)?.({ latlng: { lat: 37.41619, lng: -122.0487 } });
    await waitFor(() => expect(screen.getByText(/2\./)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Terv ellenőrzése" }));

    await waitFor(() => expect(waypointPosts).toBe(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/missions/demo-waypoint-mission",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining('"latitude_deg"'),
      }),
    );
  });

  it("reviews a point mission before it exposes the explicit start control", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/missions/point"
        ? {
          plan_id: "plan-1",
          plan_hash: "hash-1",
          summary: "Rövid út",
          goal: "Teszt",
          steps: ["TAKEOFF"],
          waypoints: [],
          approval_required: true,
        }
        : path === "/api/v1/plans/approval-token"
          ? { approval_token: "token-1", plan_hash: "hash-1", expires_in_s: 60 }
        : missionPayload(path),
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage commandsEnabled />);
    await screen.findByText(/Aktív korlát/);
    fireEvent.click(screen.getByRole("button", { name: "Terv ellenőrzése" }));

    await waitFor(() => expect(screen.getByText(/küldetés még nem indult el/i)).toBeInTheDocument());
    expect(await screen.findByRole("button", { name: /Tovább a terv-hash megerősítéséhez/ })).toBeInTheDocument();
    expect(screen.getByText("1. Javaslat elküldve")).toBeInTheDocument();
    expect(screen.getByText("2. SafetyGate ellenőrizte")).toBeInTheDocument();
    expect(screen.getByText("3. Operátori jóváhagyás szükséges")).toBeInTheDocument();
    expect(await screen.findByText(/Terv jóváhagyásra vár: Rövid út/)).toBeInTheDocument();
    expect(screen.getByText("Tervezett lépések")).toBeInTheDocument();
    expect(screen.getByText("TAKEOFF")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/missions/point",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"north_m":5') }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Tovább a terv-hash megerősítéséhez" }));
    expect(await screen.findByRole("button", { name: /Kifejezett jóváhagyás és indítás/ })).toBeInTheDocument();
  });

  it("keeps a rejected proposal out of the approval lifecycle", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/missions/point"
        ? { detail: "a cél a geofence-en kívül van" }
        : missionPayload(path),
    ), { status: path === "/api/v1/missions/point" ? 422 : 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage commandsEnabled />);
    await screen.findByText(/Aktív korlát/);
    fireEvent.click(screen.getByRole("button", { name: "Terv ellenőrzése" }));

    expect(await screen.findByText(/SafetyGate elutasította: a cél a geofence-en kívül van/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Kifejezett jóváhagyás/ })).not.toBeInTheDocument();
  });

  it("sends the bounded survey payload to the reviewed survey endpoint", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/missions/survey"
        ? { plan_id: "plan-2", summary: "Felderítés", goal: "Terület", steps: ["TAKEOFF"], waypoints: [] }
        : missionPayload(path),
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage commandsEnabled />);
    await screen.findByText(/Aktív korlát/);
    fireEvent.click(screen.getByRole("radio", { name: "Terület felderítése" }));
    fireEvent.change(screen.getByLabelText("Sugár (m)"), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "Terv ellenőrzése" }));

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/missions/survey",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"radius_m":8') }),
    ));
  });

  it("submits a map-picked point mission as a one-waypoint demo-waypoint-mission", async () => {
    let pointPosts = 0;
    let demoPosts = 0;
    const fetchMock = vi.fn((path: string, init?: RequestInit) => {
      if (path === "/api/v1/missions/point") {
        pointPosts += 1;
        return Promise.resolve(new Response(JSON.stringify({
          plan_id: "plan-point",
          summary: "Pont",
          goal: "Teszt",
          steps: [],
        }), { status: 200 }));
      }
      if (path === "/api/v1/missions/demo-waypoint-mission") {
        demoPosts += 1;
        return Promise.resolve(new Response(JSON.stringify({
          plan_id: "plan-geo",
          summary: "Térképi pont",
          goal: "Teszt",
          steps: ["TAKEOFF", "GOTO_1", "RTL", "LAND"],
          waypoints: [],
        }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify(missionPayload(path)), { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage commandsEnabled />);
    await screen.findByText(/Aktív korlát/);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/telemetry", expect.anything()));
    const map = screen.getByRole("img", { name: "Küldetési térkép" });
    vi.spyOn(map, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, width: 400, height: 400, top: 0, right: 400, bottom: 400, left: 0, toJSON: () => ({}) });

    fireEvent.click(map, { clientX: 220, clientY: 180 });

    fireEvent.click(screen.getByRole("button", { name: "Terv ellenőrzése" }));

    await waitFor(() => expect(demoPosts).toBe(1));
    expect(pointPosts).toBe(0);
    const demoCall = fetchMock.mock.calls.find(([path]) => path === "/api/v1/missions/demo-waypoint-mission");
    expect(demoCall).toBeDefined();
    const body = JSON.parse((demoCall?.[1] as RequestInit).body as string) as {
      home: { latitude_deg: number };
      waypoints: Array<{ latitude_deg: number; longitude_deg: number }>;
    };
    expect(body.waypoints).toHaveLength(1);
    expect(body.waypoints[0].latitude_deg).toBeCloseTo(37.416, 2);
    expect(body.waypoints[0].longitude_deg).toBeLessThan(-122.04);
    expect(body).not.toHaveProperty("north_m");
    expect(body).not.toHaveProperty("east_m");
    expect(body.home.latitude_deg).toBeCloseTo(37.416, 3);
  });

  it("keeps manual north/east point missions on the point endpoint without a geographic map marker", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/missions/point"
        ? { plan_id: "plan-1", summary: "Rövid út", goal: "Teszt", steps: ["TAKEOFF"], waypoints: [] }
        : missionPayload(path),
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage commandsEnabled />);
    await screen.findByText(/Aktív korlát/);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/telemetry", expect.anything()));

    expect(screen.queryByRole("button", { name: "Függő cél törlése" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Terv ellenőrzése" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/missions/point",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"north_m":5') }),
    ));
    expect(fetchMock).not.toHaveBeenCalledWith("/api/v1/missions/demo-waypoint-mission", expect.anything());
  });

  it("turns a map click into local north/east mission coordinates", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(missionPayload(path)), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    render(<MissionPage commandsEnabled />);
    await screen.findByText(/Aktív korlát/);
    const map = screen.getByRole("img", { name: "Küldetési térkép" });
    vi.spyOn(map, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, width: 400, height: 400, top: 0, right: 400, bottom: 400, left: 0, toJSON: () => ({}) });

    fireEvent.click(map, { clientX: 220, clientY: 180 });

    expect(screen.getByLabelText("Észak (m)")).toHaveValue(5);
    expect(screen.getByLabelText("Kelet (m)")).toHaveValue(5);
  });

  it("does not interpret a map click before the server safety envelope supplies its scale", () => {
    vi.stubGlobal("fetch", vi.fn((path: string) => path === "/api/v1/safety-envelope"
      ? new Promise<Response>(() => undefined)
      : Promise.resolve(new Response(JSON.stringify(missionPayload(path)), { status: 200 }))));
    render(<MissionPage commandsEnabled />);
    const map = screen.getByRole("img", { name: "Küldetési térkép" });
    vi.spyOn(map, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, width: 400, height: 400, top: 0, right: 400, bottom: 400, left: 0, toJSON: () => ({}) });

    fireEvent.click(map, { clientX: 320, clientY: 80 });

    expect(map).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByLabelText("Észak (m)")).toHaveValue(5);
    expect(screen.getByLabelText("Kelet (m)")).toHaveValue(0);
  });

  it("renders obstacle evidence from an explicitly occupancy-only document", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      missionPayload(path, { world: { occupancy_only: true, cells: [{ north_m: 2, east_m: 3, cell_size_m: 1 }] } }),
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage commandsEnabled />);
    await screen.findByText(/Aktív korlát/);

    await waitFor(() => expect(screen.getByLabelText("Mért akadálybizonyíték: É 2 m, K 3 m")).toBeInTheDocument());
  });

  it("scales the map to a chosen range rather than the safety radius", async () => {
    // The regression this guards: the map used to size itself to max_radius_m,
    // so the envelope widening to 2 km silently made one screen pixel worth ten
    // metres. The aerial basemap shrank to 18 px, a 2 m obstacle cell to a fifth
    // of one, and picking a target became meaningless -- with nothing failing.
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      missionPayload(path, {
        safety: { max_altitude_m: 20, max_radius_m: 2000, minimum_battery_percent_to_start: 35, geofence_vertices_m: [] },
        world: { occupancy_only: true, cells: [{ north_m: 10, east_m: 0, cell_size_m: 2 }] },
      }),
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage commandsEnabled />);
    await screen.findByText(/Aktív korlát/);

    const near = screen.getByLabelText("50 m") as HTMLInputElement;
    expect(near.checked).toBe(true);

    // A cell 10 m out sits a quarter of the way to the edge at the near view.
    // Scaled to the 2 km radius it would have been a fifth of a pixel from the
    // centre, indistinguishable from home.
    const cell = await screen.findByLabelText("Mért akadálybizonyíték: É 10 m, K 0 m");
    expect(Number(cell.getAttribute("y"))).toBeLessThan(160);

    fireEvent.click(screen.getByLabelText("500 m"));
    expect((screen.getByLabelText("500 m") as HTMLInputElement).checked).toBe(true);
  });

});
