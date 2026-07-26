import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatPage } from "./ChatPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("ChatPage", () => {
  it("keeps a proposal accountable from drafting through explicit approval and terminal feedback", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/chat"
        ? { text: "A terv készen áll.", status: "awaiting_approval", plan_id: "plan-42", approval_required: true }
        : path === "/api/v1/plans/approve"
          ? { text: "A küldetés elküldve.", status: "submitted", plan_id: "plan-42", approval_required: false }
          : { status: "completed", message: "A küldetés sikeresen lezárult." },
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatPage />);
    fireEvent.change(screen.getByLabelText("Küldetési kérés"), { target: { value: "Nézd meg a kaput" } });
    fireEvent.click(screen.getByRole("button", { name: "Terv készítése" }));

    expect(await screen.findByText("Jóváhagyásra vár")).toBeInTheDocument();
    expect(screen.getByText("Tervazonosító: plan-42")).toBeInTheDocument();
    expect(screen.getByText("Ellenőrzési határ: az asszisztens csak javaslatot készített.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kifejezett jóváhagyás és indítás" })).toBeInTheDocument();
    expect(screen.getByLabelText("Küldetési kérés")).toBeDisabled();
    expect(screen.getByRole("log", { name: "Küldetési beszélgetés" })).toHaveTextContent("Nézd meg a kaput");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/chat",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ text: "Nézd meg a kaput" }) }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Kifejezett jóváhagyás és indítás" }));
    expect(await screen.findByText("A küldetés elküldve.")).toBeInTheDocument();
    expect(screen.getByText("Kapcsolódó terv: plan-42")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/plans/approve",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ plan_id: "plan-42" }) }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/plans/plan-42/status",
      { cache: "no-store" },
    ));
    expect(await screen.findByText("A küldetés sikeresen lezárult.")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Lezárt: sikeres");
  });

  it("records an explicit cancellation and never calls an execution endpoint", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/chat"
        ? { text: "Javaslat készen.", status: "awaiting_approval", plan_id: "plan-cancel", approval_required: true }
        : { text: "Rendben, nem indítok küldetést.", status: "cancelled", plan_id: "plan-cancel", approval_required: false },
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatPage />);
    fireEvent.change(screen.getByLabelText("Küldetési kérés"), { target: { value: "Ellenőrizd a területet" } });
    fireEvent.click(screen.getByRole("button", { name: "Terv készítése" }));
    await screen.findByText("Jóváhagyásra vár");
    fireEvent.click(screen.getByRole("button", { name: "Terv visszavonása" }));

    expect(await screen.findByText("Rendben, nem indítok küldetést.")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Lezárt: visszavonva");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/plans/cancel",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ plan_id: "plan-cancel" }) }),
    );
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes("approve"))).toBe(false);
  });

  it("surfaces an unverifiable execution status instead of leaving a submitted plan optimistic", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/chat"
        ? { text: "A terv készen áll.", status: "awaiting_approval", plan_id: "plan-unverified", approval_required: true }
        : path === "/api/v1/plans/approve"
          ? { text: "A küldetés elküldve.", status: "submitted", plan_id: "plan-unverified", approval_required: false }
          : { detail: "status unavailable" },
    ), { status: path.includes("/status") ? 503 : 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatPage />);
    fireEvent.change(screen.getByLabelText("Küldetési kérés"), { target: { value: "Ellenőrizd a területet" } });
    fireEvent.click(screen.getByRole("button", { name: "Terv készítése" }));
    await screen.findByText("Jóváhagyásra vár");
    fireEvent.click(screen.getByRole("button", { name: "Kifejezett jóváhagyás és indítás" }));

    expect(await screen.findByText(/A végrehajtási állapot nem ellenőrizhető/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Művelet sikertelen");
  });
});
