import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";

describe("ByteWolf marketing home", () => {
  it("presents the active X500 embodiment and the safety-first runtime flow", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: /one brain/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Active X500 V2 drone embodiment")).toBeInTheDocument();
    expect(screen.getByText("Safety validation")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /explore the platform/i })).toHaveAttribute("href", "#runtime");
    expect(screen.getAllByRole("link", { name: /request developer preview/i })[0]).toHaveAttribute("href", "mailto:hello@bytewolf.ai?subject=Developer%20Preview");
  });
});
