import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EventsPage } from "./EventsPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("EventsPage", () => {
  it("shows validated immutable audit events with source, timestamp, severity, and inference boundary", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        replays: [{ id: "run-204", recorded_at: "2026-07-26T10:15:00Z", outcome: "failed", safety_decision: "blocked", terminal_phase: "failed" }],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "run-204", recorded_at: "2026-07-26T10:15:00Z", outcome: "failed", safety_decision: "blocked", terminal_phase: "failed", failure_reason: "battery low",
        events: [{ phase: "preflight", timestamp: "2026-07-26T10:12:00Z" }, { phase: "failed", timestamp: "2026-07-26T10:14:00Z" }],
        telemetry: [], preflight: { battery_percent: 4, navigation_ready: false, home_position_valid: true, global_position_valid: false },
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<EventsPage />);

    expect(await screen.findByRole("heading", { name: "Események és riasztások" })).toBeInTheDocument();
    expect(screen.getByText("run-204")).toBeInTheDocument();
    expect((await screen.findAllByText(/Forrás: küldetés-visszajátszás/)).length).toBeGreaterThan(0);
    expect(screen.getByText("2026-07-26T10:12:00Z")).toBeInTheDocument();
    expect(screen.getAllByText("RÖGZÍTETT SÚLYOSSÁG NINCS")).toHaveLength(2);
    expect(screen.getByText("SZÁRMAZTATOTT · KRITIKUS")).toBeInTheDocument();
    expect(screen.getByText(/A súlyosság csak a futás rögzített kimenetéből származik/)).toBeInTheDocument();
    expect(screen.getByText(/Rögzített hibaok: battery low/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /indítás|jóváhagyás|leállítás/i })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/missions/replays", expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/missions/replays/run-204", expect.any(Object));
  });

  it("does not render malformed audit events or derive alerts from them", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ replays: [{ id: "run-safe", recorded_at: "2026-07-26T10:15:00Z", outcome: "completed", safety_decision: "approved", terminal_phase: "completed" }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "run-safe", recorded_at: "2026-07-26T10:15:00Z", outcome: "completed", safety_decision: "approved", terminal_phase: "completed", failure_reason: null,
        events: [{ phase: "", timestamp: "not-a-time" }], telemetry: [], preflight: {},
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<EventsPage />);

    expect(await screen.findByText(/1 hibás auditesemény elutasítva/)).toBeInTheDocument();
    expect(screen.getByText("Nincs megjeleníthető, validált idővonalesemény.")).toBeInTheDocument();
    expect(screen.queryByText("SZÁRMAZTATOTT · KRITIKUS")).not.toBeInTheDocument();
  });

  it("keeps the empty and unavailable archive states explicit", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ replays: [] }), { status: 200 })));
    render(<EventsPage />);
    expect(await screen.findByRole("status")).toHaveTextContent("Nincs elérhető, ellenőrzött eseményforrás.");

    cleanup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "archive offline" }), { status: 503 })));
    render(<EventsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Az eseményarchívum nem elérhető: archive offline");
  });

  it("loads another immutable run when selected", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ replays: [
        { id: "run-new", recorded_at: "2026-07-26T10:15:00Z", outcome: "completed", safety_decision: "approved", terminal_phase: "completed" },
        { id: "run-old", recorded_at: "2026-07-25T10:15:00Z", outcome: "cancelled", safety_decision: "not-evaluated", terminal_phase: null },
      ] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "run-new", recorded_at: "2026-07-26T10:15:00Z", outcome: "completed", safety_decision: "approved", terminal_phase: "completed", failure_reason: null, events: [], telemetry: [], preflight: {} }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "run-old", recorded_at: "2026-07-25T10:15:00Z", outcome: "cancelled", safety_decision: "not-evaluated", terminal_phase: null, failure_reason: null, events: [], telemetry: [], preflight: {} }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<EventsPage />);
    await screen.findByText("run-new");
    fireEvent.click(screen.getByRole("button", { name: "Napló megnyitása: run-old" }));

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith("/api/v1/missions/replays/run-old", expect.any(Object)));
  });
});
