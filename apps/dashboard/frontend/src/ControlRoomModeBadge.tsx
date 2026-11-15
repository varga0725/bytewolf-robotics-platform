export type ControlRoomMode = "ground" | "sitl_console";

type Props = {
  mode: ControlRoomMode;
  commandsEnabled: boolean;
};

const modeCopy: Record<ControlRoomMode, { title: string; detail: string }> = {
  ground: {
    title: "Ground Control Room",
    detail: "Csak olvasható megfigyelés — parancsút nem nyit",
  },
  sitl_console: {
    title: "SITL konzol",
    detail: "Tervezési és jóváhagyási parancsok elérhetők",
  },
};

export function ControlRoomModeBadge({ mode, commandsEnabled }: Props) {
  const copy = modeCopy[mode];
  return (
    <p
      className={`control-room-mode control-room-mode--${mode}`}
      role="status"
      aria-live="polite"
      aria-label={`Üzemmód: ${copy.title}`}
    >
      <span className="eyebrow">ÜZEMMÓD</span>
      <strong>{copy.title}</strong>
      <span>{commandsEnabled ? copy.detail : "Parancsút lezárva (alapértelmezett)"}</span>
    </p>
  );
}