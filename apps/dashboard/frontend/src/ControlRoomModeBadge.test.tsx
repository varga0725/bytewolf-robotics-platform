import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ControlRoomModeBadge } from "./ControlRoomModeBadge";

afterEach(() => cleanup());

describe("ControlRoomModeBadge", () => {
  it("describes the default ground control room", () => {
    render(<ControlRoomModeBadge mode="ground" commandsEnabled={false} />);
    expect(screen.getByRole("status")).toHaveTextContent("Ground Control Room");
    expect(screen.getByRole("status")).toHaveTextContent("Parancsút lezárva");
  });

  it("describes an enabled SITL console", () => {
    render(<ControlRoomModeBadge mode="sitl_console" commandsEnabled />);
    expect(screen.getByRole("status")).toHaveTextContent("SITL konzol");
    expect(screen.getByRole("status")).toHaveTextContent("jóváhagyási parancsok");
  });
});