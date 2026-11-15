import { type ControlRoomMode } from "./ControlRoomModeBadge";

export type ControlRoomCapabilities = {
  mode: ControlRoomMode;
  commands_enabled: boolean;
  target_class?: string | null;
};

const groundCapabilities: ControlRoomCapabilities = {
  mode: "ground",
  commands_enabled: false,
};

export async function loadControlRoomCapabilities(): Promise<ControlRoomCapabilities> {
  try {
    const response = await fetch("/api/v1/capabilities", { cache: "no-store" });
    if (!response.ok) return groundCapabilities;
    const payload = (await response.json()) as Partial<ControlRoomCapabilities>;
    const mode = payload.mode === "sitl_console" ? "sitl_console" : "ground";
    return {
      mode,
      commands_enabled: payload.commands_enabled === true,
      target_class: payload.target_class ?? null,
    };
  } catch {
    return groundCapabilities;
  }
}