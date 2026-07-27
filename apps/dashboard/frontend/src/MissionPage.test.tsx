import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MissionPage } from "./MissionPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("MissionPage", () => {
  it("reviews a point mission before it exposes the explicit start control", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/safety-envelope"
        ? { max_altitude_m: 20, max_radius_m: 50, minimum_battery_percent_to_start: 35, geofence_vertices_m: [] }
        : path === "/api/v1/world-map"
          ? { cells: [] }
          : { plan_id: "plan-1", summary: "Rövid út", goal: "Teszt", steps: ["TAKEOFF"], waypoints: [] },
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage />);
    await screen.findByText(/Aktív korlát/);
    fireEvent.click(screen.getByRole("button", { name: "Terv ellenőrzése" }));

    expect(await screen.findByRole("button", { name: /Kifejezett jóváhagyás és indítás/ })).toBeInTheDocument();
    expect(screen.getByText("1. Javaslat elküldve")).toBeInTheDocument();
    expect(screen.getByText("2. SafetyGate ellenőrizte")).toBeInTheDocument();
    expect(screen.getByText("3. Operátori jóváhagyás szükséges")).toBeInTheDocument();
    expect(await screen.findByText(/Terv jóváhagyásra vár: Rövid út/)).toBeInTheDocument();
    expect(screen.getByText("Tervezett lépések")).toBeInTheDocument();
    expect(screen.getByText("TAKEOFF")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/missions/point",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"north_m":5') }),
    );
    await waitFor(() => expect(screen.getByText(/küldetés még nem indult el/i)).toBeInTheDocument());
  });

  it("keeps a rejected proposal out of the approval lifecycle", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/safety-envelope"
        ? { max_altitude_m: 20, max_radius_m: 50, minimum_battery_percent_to_start: 35, geofence_vertices_m: [] }
        : path === "/api/v1/world-map"
          ? { cells: [] }
          : { detail: "a cél a geofence-en kívül van" },
    ), { status: path === "/api/v1/missions/point" ? 422 : 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage />);
    await screen.findByText(/Aktív korlát/);
    fireEvent.click(screen.getByRole("button", { name: "Terv ellenőrzése" }));

    expect(await screen.findByText(/SafetyGate elutasította: a cél a geofence-en kívül van/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Kifejezett jóváhagyás/ })).not.toBeInTheDocument();
  });

  it("sends the bounded survey payload to the reviewed survey endpoint", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/safety-envelope"
        ? { max_altitude_m: 20, max_radius_m: 50, minimum_battery_percent_to_start: 35, geofence_vertices_m: [] }
        : path === "/api/v1/world-map"
          ? { cells: [] }
          : { plan_id: "plan-2", summary: "Felderítés", goal: "Terület", steps: ["TAKEOFF"], waypoints: [] },
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage />);
    await screen.findByText(/Aktív korlát/);
    fireEvent.click(screen.getByRole("radio", { name: "Terület felderítése" }));
    fireEvent.change(screen.getByLabelText("Sugár (m)"), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "Terv ellenőrzése" }));

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/missions/survey",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"radius_m":8') }),
    ));
  });

  it("turns a map click into local north/east mission coordinates", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(path === "/api/v1/world-map" ? { cells: [] } : { max_altitude_m: 20, max_radius_m: 50, minimum_battery_percent_to_start: 35, geofence_vertices_m: [] }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    render(<MissionPage />);
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
      : Promise.resolve(new Response(JSON.stringify({ cells: [] }), { status: 200 }))));
    render(<MissionPage />);
    const map = screen.getByRole("img", { name: "Küldetési térkép" });
    vi.spyOn(map, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, width: 400, height: 400, top: 0, right: 400, bottom: 400, left: 0, toJSON: () => ({}) });

    fireEvent.click(map, { clientX: 320, clientY: 80 });

    expect(map).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByLabelText("Észak (m)")).toHaveValue(5);
    expect(screen.getByLabelText("Kelet (m)")).toHaveValue(0);
  });

  it("renders obstacle evidence from an explicitly occupancy-only document", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/safety-envelope"
        ? { max_altitude_m: 20, max_radius_m: 50, minimum_battery_percent_to_start: 35, geofence_vertices_m: [] }
        : { occupancy_only: true, cells: [{ north_m: 2, east_m: 3, cell_size_m: 1 }] },
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage />);
    await screen.findByText(/Aktív korlát/);

    await waitFor(() => expect(screen.getByLabelText("Mért akadálybizonyíték: É 2 m, K 3 m")).toBeInTheDocument());
  });

  it("scales the map to a chosen range rather than the safety radius", async () => {
    // The regression this guards: the map used to size itself to max_radius_m,
    // so the envelope widening to 2 km silently made one screen pixel worth ten
    // metres. The aerial basemap shrank to 18 px, a 2 m obstacle cell to a fifth
    // of one, and picking a target became meaningless -- with nothing failing.
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/safety-envelope"
        ? { max_altitude_m: 20, max_radius_m: 2000, minimum_battery_percent_to_start: 35, geofence_vertices_m: [] }
        : { occupancy_only: true, cells: [{ north_m: 10, east_m: 0, cell_size_m: 2 }] },
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<MissionPage />);
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
