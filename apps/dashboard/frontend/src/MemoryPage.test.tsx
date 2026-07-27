import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MemoryPage } from "./MemoryPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("MemoryPage", () => {
  it("updates one session-scoped fact through the existing API", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ facts: [{ id: "fact-1", category: "preference", fact: "tea" }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "fact-1", category: "preference", fact: "coffee" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ facts: [{ id: "fact-1", category: "preference", fact: "coffee" }] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryPage />);
    await screen.findByText("tea");
    expect(screen.getByText(/Tárolási határ: csak ez a böngészősession/)).toBeInTheDocument();
    expect(screen.getByText(/Adatkezelés: ne ments érzékeny, azonosító vagy hitelesítési adatot/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Szerkesztés" }));
    fireEvent.change(screen.getByLabelText("Emlék szövege"), { target: { value: "coffee" } });
    fireEvent.click(screen.getByRole("button", { name: "Mentés" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/memory/fact-1",
      expect.objectContaining({ method: "PUT", body: expect.stringContaining('"fact":"coffee"') }),
    ));
    expect(await screen.findByText("Az emlék mentve, a session-memória frissítve.")).toBeInTheDocument();
  });

  it("explains a failed session-memory refresh and lets the operator recover", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "A kapcsolat megszakadt." }), { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ facts: [{ id: "fact-2", category: "name", fact: "Byte" }] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("A memória nem olvasható: A kapcsolat megszakadt.");
    expect(screen.getByText("Próbáld meg újra a frissítést.")).toBeInTheDocument();
    expect(screen.queryByText("Nincs megjeleníthető emlék.")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Frissítés" }));

    expect(await screen.findByText("Byte")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("A session-memória frissítve.");
  });
});
