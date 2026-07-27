import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReplayPage } from "./ReplayPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("ReplayPage", () => {
  it("shows immutable run history, then the selected run's terminal evidence", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        replays: [
          { id: "run-202", recorded_at: "2026-07-25T09:15:00Z", outcome: "completed", safety_decision: "approved", terminal_phase: "completed" },
          { id: "run-201", recorded_at: "2026-07-24T15:30:00Z", outcome: "failed", safety_decision: "rejected", terminal_phase: "failed" },
        ],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "run-202",
        recorded_at: "2026-07-25T09:15:00Z",
        outcome: "completed",
        safety_decision: "approved",
        failure_reason: null,
        events: [{ phase: "takeoff", timestamp: "2026-07-25T09:15:03Z" }],
        terminal_phase: "completed",
        telemetry: [],
        preflight: { battery_percent: 87, navigation_ready: true, home_position_valid: true, global_position_valid: true },
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<ReplayPage />);

    expect(await screen.findByText("run-202")).toBeInTheDocument();
    expect(screen.getByText(/Csak olvasható küldetéstörténet/)).toBeInTheDocument();
    expect(await screen.findByText("takeoff")).toBeInTheDocument();
    expect(screen.getByText("Sikeresen befejeződött")).toBeInTheDocument();
    expect(screen.getAllByText("SafetyGate jóváhagyta")).not.toHaveLength(0);
    expect(screen.getByText("Végső fázis")).toBeInTheDocument();
    expect(screen.getByText(/BIZONYÍTÉK LÁNCA/)).toBeInTheDocument();
    expect(screen.getAllByText("completed")).not.toHaveLength(0);
    expect(screen.getByText("87%")).toBeInTheDocument();
    expect(screen.getAllByText("rendben")).toHaveLength(3);
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/missions/replays", expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/missions/replays/run-202", expect.any(Object));
  });

  it("makes a blocked run's safety and failed preflight evidence legible", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ replays: [{ id: "run-blocked", recorded_at: "2026-07-25T09:15:00Z", outcome: "failed", safety_decision: "blocked", terminal_phase: "failed" }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "run-blocked", recorded_at: "2026-07-25T09:15:00Z", outcome: "failed", safety_decision: "blocked", failure_reason: "battery low", terminal_phase: "failed", telemetry: [], events: [],
        preflight: { battery_percent: 4, navigation_ready: false, home_position_valid: true, global_position_valid: false },
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<ReplayPage />);

    expect(await screen.findByText("SafetyGate blokkolta")).toBeInTheDocument();
    expect(screen.getByText("Sikertelenül zárult")).toBeInTheDocument();
    expect(screen.getByText("4%")).toBeInTheDocument();
    expect(screen.getAllByText("nem volt rendben")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /indítás|jóváhagyás|visszavonás/i })).not.toBeInTheDocument();
  });

  it("loads the clicked historical run without offering mission controls", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        replays: [
          { id: "safe/run", recorded_at: "old", outcome: "completed", safety_decision: "approved", terminal_phase: "completed" },
          { id: "failed run", recorded_at: "new", outcome: "failed", safety_decision: "blocked", terminal_phase: "failed" },
        ],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "safe/run", recorded_at: "old", outcome: "completed", safety_decision: "approved", failure_reason: null, terminal_phase: "completed",
        events: [], telemetry: [], preflight: { battery_percent: 50, navigation_ready: true, home_position_valid: true, global_position_valid: true },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "failed run", recorded_at: "new", outcome: "failed", safety_decision: "blocked", failure_reason: "battery low", terminal_phase: "failed",
        events: [{ phase: "preflight", timestamp: "new" }], telemetry: [], preflight: { battery_percent: 4, navigation_ready: false, home_position_valid: true, global_position_valid: false },
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<ReplayPage />);
    await screen.findByText("safe/run");
    fireEvent.click(screen.getByRole("button", { name: /Visszajátszás megnyitása: failed run/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/missions/replays/failed%20run",
      expect.any(Object),
    ));
    expect(await screen.findByText("battery low")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve|start|cancel|control/i })).not.toBeInTheDocument();
  });

  it("reports unavailable replay history without fabricating a run", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "archive offline" }), { status: 503 })));

    render(<ReplayPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("A visszajátszási előzmény nem elérhető: archive offline");
    expect(screen.queryByText("Végállapot")).not.toBeInTheDocument();
  });
});
