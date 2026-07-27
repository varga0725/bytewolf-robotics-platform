import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

afterEach(() => {
  document.body.replaceChildren();
});

function BrokenView(): never {
  throw new Error("unexpected render failure");
}

describe("ErrorBoundary", () => {
  it("keeps the control room actionable when a child view throws", () => {
    render(<ErrorBoundary><BrokenView /></ErrorBoundary>);

    expect(screen.getByRole("alert")).toHaveTextContent("A Control Room nézet hibába futott.");
    expect(screen.getByRole("button", { name: "Oldal újratöltése" })).toBeInTheDocument();
  });
});
