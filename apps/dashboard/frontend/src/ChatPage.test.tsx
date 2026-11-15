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
        ? { text: "A terv készen áll.", status: "awaiting_approval", plan_id: "plan-42", plan_hash: "hash-42", approval_required: true }
        : path === "/api/v1/plans/approval-token"
          ? { approval_token: "token-42", plan_hash: "hash-42", expires_in_s: 300 }
        : path === "/api/v1/plans/approve"
          ? { text: "A küldetés elküldve.", status: "submitted", plan_id: "plan-42", approval_required: false }
          : { status: "completed", message: "A küldetés sikeresen lezárult." },
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatPage commandsEnabled />);
    fireEvent.change(screen.getByLabelText("Küldetési kérés"), { target: { value: "Nézd meg a kaput" } });
    fireEvent.click(screen.getByRole("button", { name: "Terv készítése" }));

    expect(await screen.findByText("Jóváhagyásra vár")).toBeInTheDocument();
    expect(screen.getByText("Tervazonosító: plan-42")).toBeInTheDocument();
    expect(screen.getByText("Ellenőrzési határ: az asszisztens csak javaslatot készített.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tovább a terv-hash megerősítéséhez" })).toBeInTheDocument();
    expect(screen.getByLabelText("Küldetési kérés")).toBeDisabled();
    expect(screen.getByRole("log", { name: "Küldetési beszélgetés" })).toHaveTextContent("Nézd meg a kaput");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/chat",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ text: "Nézd meg a kaput" }) }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Tovább a terv-hash megerősítéséhez" }));
    expect(await screen.findByText("Terv-hash megerősítése")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Kifejezett jóváhagyás és indítás" }));
    expect(await screen.findByText("A küldetés elküldve.")).toBeInTheDocument();
    expect(screen.getByText("Kapcsolódó terv: plan-42")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/plans/approval-token",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ plan_id: "plan-42" }) }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/plans/approve",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ plan_id: "plan-42", approval_token: "token-42" }) }),
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

    render(<ChatPage commandsEnabled />);
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

  it("keeps non-flight chat available but hides flight approval in ground mode", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/chat"
        ? { text: "Szia!", status: "conversation", plan_id: null, approval_required: false }
        : {},
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatPage commandsEnabled={false} />);
    expect(screen.getByLabelText("Üzenet")).toBeInTheDocument();
    expect(screen.queryByLabelText("Küldetési kérés")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Kifejezett jóváhagyás és indítás" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Üzenet"), { target: { value: "Mi a státusz?" } });
    fireEvent.click(screen.getByRole("button", { name: "Küldés" }));
    await screen.findByText("Szia!");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/chat",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ text: "Mi a státusz?" }) }),
    );
  });

  it("resets confirmation after a failed approve so a fresh token can be requested", async () => {
    const fetchMock = vi.fn((path: string, init?: RequestInit) => {
      if (path === "/api/v1/chat") {
        return Promise.resolve(new Response(JSON.stringify(
          { text: "A terv készen áll.", status: "awaiting_approval", plan_id: "plan-retry", plan_hash: "hash-retry", approval_required: true },
        ), { status: 200 }));
      }
      if (path === "/api/v1/plans/approval-token") {
        return Promise.resolve(new Response(JSON.stringify(
          { approval_token: "token-retry", plan_hash: "hash-retry", expires_in_s: 300 },
        ), { status: 200 }));
      }
      if (path === "/api/v1/plans/approve") {
        return Promise.resolve(new Response(JSON.stringify({ detail: "Approval token expired" }), { status: 403 }));
      }
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatPage commandsEnabled />);
    fireEvent.change(screen.getByLabelText("Küldetési kérés"), { target: { value: "Teszt" } });
    fireEvent.click(screen.getByRole("button", { name: "Terv készítése" }));
    await screen.findByText("Jóváhagyásra vár");
    fireEvent.click(screen.getByRole("button", { name: "Tovább a terv-hash megerősítéséhez" }));
    await screen.findByText("Terv-hash megerősítése");
    fireEvent.click(screen.getByRole("button", { name: "Kifejezett jóváhagyás és indítás" }));
    expect(await screen.findByText(/jóváhagyás nem sikerült/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tovább a terv-hash megerősítéséhez" })).toBeInTheDocument();
  });

  it("surfaces an unverifiable execution status instead of leaving a submitted plan optimistic", async () => {
    const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/chat"
        ? { text: "A terv készen áll.", status: "awaiting_approval", plan_id: "plan-unverified", approval_required: true }
        : path === "/api/v1/plans/approval-token"
          ? { approval_token: "token-unverified", plan_hash: "hash-unverified", expires_in_s: 300 }
        : path === "/api/v1/plans/approve"
          ? { text: "A küldetés elküldve.", status: "submitted", plan_id: "plan-unverified", approval_required: false }
          : { detail: "status unavailable" },
    ), { status: path.includes("/status") ? 503 : 200 })));
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatPage commandsEnabled />);
    fireEvent.change(screen.getByLabelText("Küldetési kérés"), { target: { value: "Ellenőrizd a területet" } });
    fireEvent.click(screen.getByRole("button", { name: "Terv készítése" }));
    await screen.findByText("Jóváhagyásra vár");
    fireEvent.click(screen.getByRole("button", { name: "Tovább a terv-hash megerősítéséhez" }));
    await screen.findByText("Terv-hash megerősítése");
    fireEvent.click(screen.getByRole("button", { name: "Kifejezett jóváhagyás és indítás" }));

    expect(await screen.findByText(/A végrehajtási állapot nem ellenőrizhető/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Művelet sikertelen");
  });
});
