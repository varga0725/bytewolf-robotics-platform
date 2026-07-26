import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnalyticsPage } from "./AnalyticsPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("AnalyticsPage", () => {
  it("derives only verified outcome and terminal-phase counts from matching immutable archives", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ replays: [
        { id: "run-02", recorded_at: "2026-07-25T09:15:00Z", outcome: "completed", safety_decision: "approved", terminal_phase: "completed" },
        { id: "run-01", recorded_at: "2026-07-24T15:30:00Z", outcome: "failed", safety_decision: "blocked", terminal_phase: "failed" },
      ] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "run-02", recorded_at: "2026-07-25T09:15:00Z", outcome: "completed", safety_decision: "approved", terminal_phase: "completed", events: [],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "run-01", recorded_at: "2026-07-24T15:30:00Z", outcome: "failed", safety_decision: "blocked", terminal_phase: "failed", events: [{ phase: "preflight", timestamp: "2026-07-24T15:29:59Z" }],
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AnalyticsPage />);

    expect(await screen.findByText("2 ellenőrzött futás")).toBeInTheDocument();
    expect(screen.getByText("Sikeresen befejeződött")).toBeInTheDocument();
    expect(screen.getByText("Sikertelenül zárult")).toBeInTheDocument();
    expect(screen.getAllByText("1 futás")).toHaveLength(4);
    expect(screen.getByText("Első ellenőrzött futás")).toBeInTheDocument();
    expect(screen.getByText("2026-07-24T15:30:00Z")).toBeInTheDocument();
    expect(screen.getByText(/csak az itt felsorolt, részletarchívummal egyező futásokból/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/missions/replays", expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/missions/replays/run-02", expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/v1/missions/replays/run-01", expect.any(Object));
  });

  it("fails closed for malformed, mismatched, and unavailable archive details", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ replays: [
        { id: "valid", recorded_at: "2026-07-25T09:15:00Z", outcome: "completed", safety_decision: "approved", terminal_phase: "completed" },
        { id: "mismatch", recorded_at: "2026-07-24T15:30:00Z", outcome: "failed", safety_decision: "blocked", terminal_phase: "failed" },
        { id: "broken", recorded_at: "not-a-time", outcome: "completed", safety_decision: "approved", terminal_phase: "completed" },
      ] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "valid", recorded_at: "2026-07-25T09:15:00Z", outcome: "completed", safety_decision: "approved", terminal_phase: "completed", events: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "mismatch", recorded_at: "2026-07-24T15:30:00Z", outcome: "completed", safety_decision: "blocked", terminal_phase: "failed", events: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AnalyticsPage />);

    expect(await screen.findByText("1 ellenőrzött futás")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("2 archívumbejegyzés nem ellenőrizhető");
    expect(screen.queryByText("Sikertelenül zárult")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("reports unavailable history without inventing analytics", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "archive offline" }), { status: 503 })));

    render(<AnalyticsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Az analitikai archívum nem elérhető: archive offline");
    expect(screen.queryByText(/ellenőrzött futás/)).not.toBeInTheDocument();
  });
});
