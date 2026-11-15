import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TargetClassBadge, TargetClassDangerBanner } from "./TargetClassBadge";

afterEach(() => {
  cleanup();
});

describe("TargetClassBadge", () => {
  it("renders verified styling without an alert banner", () => {
    const { container } = render(
      <>
        <TargetClassBadge targetClass="sitl_verified" />
        <TargetClassDangerBanner targetClass="sitl_verified" />
      </>,
    );
    expect(container.querySelector(".target-class-badge--sitl_verified")).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows an alert banner for unknown targets", () => {
    render(<TargetClassDangerBanner targetClass="unknown" />);
    expect(screen.getByRole("alert")).toHaveTextContent("ISMERETLEN CÉL");
  });
});