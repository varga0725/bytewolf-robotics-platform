import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CameraPage } from "./CameraPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mockDetections(document: unknown, ok = true) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok, json: async () => document }));
}

describe("CameraPage", () => {
  it("starts with the front camera stream", () => {
    render(<CameraPage />);

    expect(screen.getByRole("img", { name: "Elülső kamera élőképe" })).toHaveAttribute(
      "src",
      "/api/v1/cameras/front/stream",
    );
  });

  it("switches the stream when the operator selects another sensor", () => {
    render(<CameraPage />);

    fireEvent.change(screen.getByLabelText("Kamera forrása"), {
      target: { value: "down" },
    });

    expect(screen.getByRole("img", { name: "Alsó kamera élőképe" })).toHaveAttribute(
      "src",
      "/api/v1/cameras/down/stream",
    );
  });

  it("presents only fresh, well-formed detections as camera evidence", async () => {
    mockDetections({
      validity: "valid",
      captured_at: new Date().toISOString(),
      max_age_s: 30,
      frame: { width: 640, height: 480, frame_id: "front-42" },
      detections: [
        { label: "leszállóhely", confidence: 0.92, bbox: { x: 12, y: 24, width: 100, height: 80 } },
      ],
    });

    render(<CameraPage />);

    expect(await screen.findByText(/friss, érvényes bizonyíték/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Érvényes objektumészlelések")).toBeInTheDocument();
    expect(screen.getByText(/front-42/i)).toBeInTheDocument();
    expect(screen.getByText(/Bizonyossági jelzés: 92%/i)).toBeInTheDocument();
  });

  it("fails closed when any detection record is malformed", async () => {
    mockDetections({
      validity: "valid",
      captured_at: new Date().toISOString(),
      max_age_s: 30,
      frame: { width: 640, height: 480 },
      detections: [
        { label: "jó", confidence: 0.8, bbox: { x: 10, y: 10, width: 20, height: 20 } },
        { label: "kilóg", confidence: 0.8, bbox: { x: 630, y: 10, width: 20, height: 20 } },
        { label: "bizonytalan", confidence: 2, bbox: { x: 10, y: 10, width: 20, height: 20 } },
      ],
    });

    render(<CameraPage />);

    expect(await screen.findByText(/észlelési bizonyíték érvénytelen/i)).toBeInTheDocument();
    expect(screen.queryByText(/jó/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/kilóg/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/bizonytalan/i)).not.toBeInTheDocument();
  });

  it("withholds stale or unavailable evidence and tells the operator why", async () => {
    mockDetections({
      validity: "valid",
      captured_at: "2020-01-01T00:00:00Z",
      max_age_s: 0.5,
      frame: { width: 640, height: 480 },
      detections: [{ label: "régi", confidence: 0.9, bbox: { x: 1, y: 1, width: 20, height: 20 } }],
    });

    render(<CameraPage />);

    expect(await screen.findByText(/elavult bizonyíték/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Érvényes objektumészlelések")).not.toBeInTheDocument();
    expect(screen.queryByText(/régi/i)).not.toBeInTheDocument();
  });

  it("shows an unavailable state when the detections endpoint fails", async () => {
    mockDetections({}, false);

    render(<CameraPage />);

    expect(await screen.findByText(/bizonyíték nem elérhető/i)).toBeInTheDocument();
  });
});
