import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "./SettingsPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

function mockSettingsApi(overrides: Record<string, unknown> = {}) {
  const responses: Record<string, unknown> = {
    "/api/v1/telemetry": { captured_at: new Date().toISOString(), in_air: false, battery_percent: 72 },
    "/api/v1/safety-envelope": { max_altitude_m: 20, max_radius_m: 50, minimum_battery_percent_to_start: 40 },
    ...overrides,
  };
  vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(responses[path]), { status: responses[path] === undefined ? 404 : 200 }))));
}

describe("SettingsPage", () => {
  it("separates the server-enforced safety profile from local display preferences and controls", async () => {
    mockSettingsApi();
    render(<SettingsPage />);

    expect(await screen.findByRole("heading", { name: "Rendszerbeállítások" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Szerver által rögzített safety profil" })).toHaveTextContent("20 m");
    expect(screen.getByRole("region", { name: "Szerver által rögzített safety profil" })).toHaveTextContent("50 m");
    expect(screen.getByRole("region", { name: "Helyi megjelenítési preferenciák" })).toHaveTextContent("Csak ezen a böngészőn");
    expect(screen.getByRole("region", { name: "Nem konfigurálható vezérlési felületek" })).toHaveTextContent("NEM ELÉRHETŐ");
    expect(screen.getByText(/A safety korlátok nem helyi beállítások/i)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("fails closed when the safety profile or telemetry evidence is malformed or stale", async () => {
    mockSettingsApi({
      "/api/v1/telemetry": { captured_at: "2020-01-01T00:00:00Z", in_air: true, battery_percent: 72 },
      "/api/v1/safety-envelope": { max_altitude_m: 0, max_radius_m: 50, minimum_battery_percent_to_start: 40 },
    });
    render(<SettingsPage />);

    expect(await screen.findByText("SAFETY PROFIL NEM ELLENŐRIZHETŐ")).toBeInTheDocument();
    expect(screen.getByText("TELEMETRIA ELAVULT")).toBeInTheDocument();
    expect(screen.getAllByText("NEM ELLENŐRIZHETŐ").length).toBeGreaterThan(2);
    expect(screen.queryByText("20 m")).not.toBeInTheDocument();
  });
});
