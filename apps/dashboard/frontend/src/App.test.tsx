import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App telemetry status", () => {
  it("announces a fresh sample with its capture time and age", async () => {
    const capturedAt = new Date(Date.now() - 5_000).toISOString();
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              position: {
                latitude_deg: 47.4979,
                longitude_deg: 19.0402,
                absolute_altitude_m: 125.5,
                relative_altitude_m: 12.3,
              },
              battery_percent: 74,
              in_air: false,
              heading_deg: 271.4,
              captured_at: capturedAt,
            }),
            { status: 200 },
          ),
        ),
      ),
    );

    render(<App />);

    const summary = await screen.findByRole("region", { name: "Élő telemetria állapot" });
    expect(summary).toHaveTextContent("Telemetria kapcsolódva");
    expect(summary).toHaveTextContent(`Rögzítés ideje: ${capturedAt}`);
    expect(summary).toHaveTextContent(/másodperce/);
    expect(screen.getByRole("region", { name: "Telemetria integritása" })).toHaveTextContent("5 / 5 adatmező ellenőrizve");
    expect(screen.getByText("271,4°")).toBeInTheDocument();
    expect(screen.getAllByText("ÉLŐ MINTA")).toHaveLength(2);
  });

  it("keeps stale measurements visible but explicitly warns they are not current", async () => {
    const capturedAt = new Date(Date.now() - 30_000).toISOString();
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              position: null,
              battery_percent: 74,
              in_air: false,
              captured_at: capturedAt,
            }),
            { status: 200 },
          ),
        ),
      ),
    );

    render(<App />);

    const summary = await screen.findByRole("region", { name: "Élő telemetria állapot" });
    expect(summary).toHaveTextContent("Telemetria elavult");
    expect(summary).toHaveTextContent("Az értékek korábbi mérést mutatnak, nem aktuálisak.");
    expect(summary).toHaveTextContent(/másodperce/);
    expect(screen.getByText("74,0 %")).toBeInTheDocument();
  });

  it("marks all state values unavailable when the telemetry request fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));

    render(<App />);

    const summary = await screen.findByRole("region", { name: "Élő telemetria állapot" });
    expect(summary).toHaveTextContent("Telemetria nem elérhető");
    expect(summary).toHaveTextContent("Az állapotértékek nem tekinthetők aktuálisnak.");
    expect(within(screen.getByRole("region", { name: "Rendszerállapot" })).getAllByText("NEM ELÉRHETŐ")).toHaveLength(5);
  });

  it("fails closed when an API sample contains an invalid number or a future capture time", async () => {
    const capturedAt = new Date(Date.now() + 60_000).toISOString();
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              position: null,
              battery_percent: 101,
              in_air: true,
              heading_deg: Number.POSITIVE_INFINITY,
              captured_at: capturedAt,
            }),
            { status: 200 },
          ),
        ),
      ),
    );

    render(<App />);

    const summary = await screen.findByRole("region", { name: "Élő telemetria állapot" });
    expect(summary).toHaveTextContent("Telemetria nem elérhető");
    expect(summary).not.toHaveTextContent(capturedAt);
    expect(within(screen.getByRole("region", { name: "Rendszerállapot" })).getAllByText("NEM ELÉRHETŐ")).toHaveLength(5);
    expect(screen.getByRole("region", { name: "Telemetria integritása" })).toHaveTextContent("0 / 5 adatmező ellenőrizve");
  });
});

describe("App view navigation", () => {
  it("exposes the views as a labelled tablist connected to the current panel", () => {
    render(<App />);

    const tablist = screen.getByRole("tablist", { name: "Control Room nézetek" });
    const stateTab = screen.getByRole("tab", { name: "Állapot" });
    const panel = screen.getByRole("tabpanel", { name: "Állapot" });

    expect(tablist).toContainElement(stateTab);
    expect(stateTab).toHaveAttribute("aria-selected", "true");
    expect(stateTab).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", stateTab.id);
    for (const tab of screen.getAllByRole("tab")) {
      const panelId = tab.getAttribute("aria-controls");
      expect(panelId).not.toBeNull();
      expect(document.getElementById(panelId!)).toHaveAttribute("role", "tabpanel");
    }
    expect(screen.getByText("Aktív nézet: Állapot")).toHaveAttribute("aria-live", "polite");
  });

  it("moves selection and focus with arrow, Home, and End keys", () => {
    render(<App />);

    const stateTab = screen.getByRole("tab", { name: "Állapot" });
    stateTab.focus();
    fireEvent.keyDown(stateTab, { key: "ArrowRight" });

    const cameraTab = screen.getByRole("tab", { name: "Kamera" });
    expect(cameraTab).toHaveFocus();
    expect(cameraTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "Kamera" })).toBeInTheDocument();

    fireEvent.keyDown(cameraTab, { key: "End" });
    const settingsTab = screen.getByRole("tab", { name: "Beállítások" });
    expect(settingsTab).toHaveFocus();
    expect(settingsTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(settingsTab, { key: "Home" });
    expect(stateTab).toHaveFocus();
    expect(stateTab).toHaveAttribute("aria-selected", "true");
  });

  it("opens a keyboard quick navigator, filters existing views, and returns focus on escape", () => {
    render(<App />);

    const trigger = screen.getByRole("button", { name: /gyors navigáció megnyitása/i });
    trigger.focus();
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    const dialog = screen.getByRole("dialog", { name: "Gyors nézetváltó" });
    const search = within(dialog).getByRole("combobox", { name: "Nézet keresése" });
    expect(search).toHaveFocus();
    expect(dialog).toHaveTextContent("Csak nézetváltás");

    fireEvent.change(search, { target: { value: "kamera" } });
    expect(within(dialog).getByRole("option", { name: /kamera/i })).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: /küldetés/i })).not.toBeInTheDocument();

    fireEvent.keyDown(search, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Gyors nézetváltó" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("keeps focus in the quick navigator and returns it to the keyboard opener", () => {
    render(<App />);

    const stateTab = screen.getByRole("tab", { name: "Állapot" });
    stateTab.focus();
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });

    const dialog = screen.getByRole("dialog", { name: "Gyors nézetváltó" });
    const close = within(dialog).getByRole("button", { name: "Bezárás" });
    close.focus();
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(within(dialog).getByRole("option", { name: "Beállítások" })).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(stateTab).toHaveFocus();
  });

  it("does not reset quick navigation from an editable field shortcut", () => {
    render(<App />);

    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    const search = screen.getByRole("combobox", { name: "Nézet keresése" });
    fireEvent.change(search, { target: { value: "kamera" } });
    fireEvent.keyDown(search, { key: "k", ctrlKey: true });

    expect(search).toHaveValue("kamera");
  });

  it("navigates to the selected existing view without exposing a control action", () => {
    render(<App />);

    fireEvent.keyDown(document, { key: "k", metaKey: true });
    const dialog = screen.getByRole("dialog", { name: "Gyors nézetváltó" });
    const search = within(dialog).getByRole("combobox", { name: "Nézet keresése" });
    fireEvent.change(search, { target: { value: "világ" } });
    fireEvent.keyDown(search, { key: "Enter" });

    expect(screen.queryByRole("dialog", { name: "Gyors nézetváltó" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Világ" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Aktív nézet: Világ")).toBeInTheDocument();
  });

  it("includes the Knowledge view in both tabs and quick navigation", () => {
    render(<App />);

    expect(screen.getByRole("tab", { name: "Tudás" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    const dialog = screen.getByRole("dialog", { name: "Gyors nézetváltó" });
    expect(within(dialog).getByRole("option", { name: "Tudás" })).toBeInTheDocument();
  });

  it("includes Live Operations and Events & Logs in both operator navigation surfaces", () => {
    render(<App />);

    expect(screen.getByRole("tab", { name: "Élő műveletek" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Események és naplók" })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    const dialog = screen.getByRole("dialog", { name: "Gyors nézetváltó" });
    expect(within(dialog).getByRole("option", { name: "Élő műveletek" })).toBeInTheDocument();
    expect(within(dialog).getByRole("option", { name: "Események és naplók" })).toBeInTheDocument();
  });

  it("includes the fleet, perception, and cognitive runtime read-only views in both navigation surfaces", () => {
    render(<App />);

    expect(screen.getByRole("tab", { name: "Robotok" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Percepció" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Kognitív futtatókörnyezet" })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    const dialog = screen.getByRole("dialog", { name: "Gyors nézetváltó" });
    expect(within(dialog).getByRole("option", { name: "Robotok" })).toBeInTheDocument();
    expect(within(dialog).getByRole("option", { name: "Percepció" })).toBeInTheDocument();
    expect(within(dialog).getByRole("option", { name: "Kognitív futtatókörnyezet" })).toBeInTheDocument();
  });

  it("includes verified analytics, developer diagnostics, and settings in both navigation surfaces", () => {
    render(<App />);

    expect(screen.getByRole("tab", { name: "Analitika" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Fejlesztői diagnosztika" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Beállítások" })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    const dialog = screen.getByRole("dialog", { name: "Gyors nézetváltó" });
    expect(within(dialog).getByRole("option", { name: "Analitika" })).toBeInTheDocument();
    expect(within(dialog).getByRole("option", { name: "Fejlesztői diagnosztika" })).toBeInTheDocument();
    expect(within(dialog).getByRole("option", { name: "Beállítások" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Csak nézetváltás");
  });
});

describe("App overview context", () => {
  it("summarizes only the telemetry freshness and existing no-control boundary", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));

    render(<App />);

    const context = await screen.findByRole("region", { name: "Működési kontextus" });
    expect(context).toHaveTextContent("Telemetria nem elérhető");
    expect(context).toHaveTextContent("nincs közvetlen vezérlés");
    expect(context).toHaveTextContent("külön jóváhagyás");
  });

  it("keeps platform, safety, connected-body, mission, alert, and latest-sample context together in the upper status strip", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));

    render(<App />);

    const strip = await screen.findByRole("region", { name: "Operátori rendszerállapot" });
    expect(strip).toHaveTextContent("Jóváhagyás-köteles");
    expect(strip).toHaveTextContent("Szimuláció");
    expect(strip).toHaveTextContent("NEM ELLENŐRIZHETŐ");
    expect(strip).toHaveTextContent("1 SZIMULÁLT TEST");
    expect(strip).toHaveTextContent("Nincs ellenőrzött aktív küldetésadat");
    expect(strip).toHaveTextContent("Nincs ellenőrzött riasztási feed");
    expect(strip).toHaveTextContent("Nincs közvetlen vezérlés");
  });
});
