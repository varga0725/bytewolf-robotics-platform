import { useEffect, useState } from "react";

import { api } from "./api";

type TelemetryState = "fresh" | "stale" | "unavailable";
type CameraState = "fresh" | "stale" | "unavailable";
type Telemetry = {
  capturedAt: string;
  inAir: boolean | null;
  battery: number | null;
  location: string | null;
};
type SafetyProfile = { maxAltitude: number; maxRadius: number; minBattery: number };
type RobotState = {
  telemetry: Telemetry | null;
  telemetryState: TelemetryState;
  safety: SafetyProfile | null;
  cameraState: CameraState;
  checkedAt: string | null;
};

const initialState: RobotState = {
  telemetry: null,
  telemetryState: "unavailable",
  safety: null,
  cameraState: "unavailable",
  checkedAt: null,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function timestamp(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function readTelemetry(value: unknown): Telemetry | null {
  if (!isRecord(value) || !timestamp(value.captured_at)) return null;
  const position = isRecord(value.position) ? value.position : null;
  const latitude = position?.latitude_deg;
  const longitude = position?.longitude_deg;
  return {
    capturedAt: value.captured_at,
    inAir: typeof value.in_air === "boolean" ? value.in_air : null,
    battery: finite(value.battery_percent) && value.battery_percent >= 0 && value.battery_percent <= 100 ? value.battery_percent : null,
    location: finite(latitude) && finite(longitude) && latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180
      ? `${latitude.toFixed(6).replace(".", ",")}, ${longitude.toFixed(6).replace(".", ",")}`
      : null,
  };
}

function getTelemetryState(telemetry: Telemetry | null): TelemetryState {
  if (!telemetry) return "unavailable";
  return Date.now() - Date.parse(telemetry.capturedAt) > 10_000 ? "stale" : "fresh";
}

function readSafety(value: unknown): SafetyProfile | null {
  if (!isRecord(value)) return null;
  const maxAltitude = value.max_altitude_m;
  const maxRadius = value.max_radius_m;
  const minBattery = value.minimum_battery_percent_to_start;
  if (!finite(maxAltitude) || !finite(maxRadius) || !finite(minBattery) || maxAltitude <= 0 || maxRadius <= 0 || minBattery < 0 || minBattery > 100) return null;
  return { maxAltitude, maxRadius, minBattery };
}

function readCameraState(value: unknown): CameraState {
  if (!isRecord(value) || value.validity !== "valid" || !timestamp(value.captured_at) || !finite(value.max_age_s) || value.max_age_s <= 0 || !Array.isArray(value.detections) || !isRecord(value.frame)) return "unavailable";
  if (Date.now() - Date.parse(value.captured_at) > value.max_age_s * 1000) return "stale";
  return "fresh";
}

function metric(value: number | null): string {
  return value === null ? "NEM ELLENŐRIZHETŐ" : `${value.toFixed(1).replace(".", ",")} %`;
}

function sampleLabel(state: TelemetryState): string {
  return state === "fresh" ? "KAPCSOLÓDVA" : state === "stale" ? "TELEMETRIA ELAVULT" : "TELEMETRIA NEM ELÉRHETŐ";
}

function flightLabel(telemetry: Telemetry | null, state: TelemetryState): string {
  if (state !== "fresh") return "NEM ELLENŐRIZHETŐ";
  if (telemetry?.inAir === true) return "LEVEGŐBEN";
  if (telemetry?.inAir === false) return "FÖLDÖN";
  return "NEM ELLENŐRIZHETŐ";
}

function checkedAt(value: string | null): string {
  return value ? new Date(value).toLocaleTimeString("hu-HU") : "NEM ELLENŐRIZHETŐ";
}

export function RobotsPage() {
  const [state, setState] = useState<RobotState>(initialState);

  useEffect(() => {
    let active = true;
    void Promise.allSettled([
      api<unknown>("/api/v1/telemetry"),
      api<unknown>("/api/v1/safety-envelope"),
      api<unknown>("/api/v1/cameras/front/detections"),
    ]).then(([telemetryResult, safetyResult, cameraResult]) => {
      if (!active) return;
      const telemetry = telemetryResult.status === "fulfilled" ? readTelemetry(telemetryResult.value) : null;
      setState({
        telemetry,
        telemetryState: getTelemetryState(telemetry),
        safety: safetyResult.status === "fulfilled" ? readSafety(safetyResult.value) : null,
        cameraState: cameraResult.status === "fulfilled" ? readCameraState(cameraResult.value) : "unavailable",
        checkedAt: new Date().toISOString(),
      });
    });
    return () => { active = false; };
  }, []);

  const telemetryUsable = state.telemetryState === "fresh";
  const safetyMode = state.safety ? "SAFETY PROFIL OLVASHATÓ" : "SAFETY MÓD NEM ELLENŐRIZHETŐ";
  const cameraLabel = state.cameraState === "fresh" ? "KAMERA-BIZONYÍTÉK FRISS" : state.cameraState === "stale" ? "KAMERA-BIZONYÍTÉK ELAVULT" : "KAMERA-BIZONYÍTÉK NEM ELÉRHETŐ";

  return <section className="content-card" aria-labelledby="robot-page-title">
    <div className="section-heading">
      <div><p className="eyebrow">ROBOT RÉSZLET · CSAK OLVASHATÓ</p><h2 id="robot-page-title">Robot</h2></div>
      <p className={`telemetry-sample telemetry-sample--${state.telemetryState}`}>{sampleLabel(state.telemetryState)}</p>
    </div>
    <p className="muted" role="status">Egyetlen, a szimuláció által szolgáltatott body jelenik meg. Utolsó ellenőrzés: {checkedAt(state.checkedAt)}.</p>

    <div className="live-operations-grid">
      <section className="live-operations-zone" aria-labelledby="robot-identity-title">
        <p className="eyebrow">SZIMULÁCIÓS BODY · 01</p><h3 id="robot-identity-title">Azonosítás és kapcsolat</h3>
        <dl className="telemetry-details">
          <div><dt>Telemetria-kapcsolat</dt><dd>{sampleLabel(state.telemetryState)}</dd></div>
          <div><dt>Repülési állapot</dt><dd>{flightLabel(state.telemetry, state.telemetryState)}</dd></div>
          <div><dt>Aktív küldetés</dt><dd>NEM IGAZOLHATÓ</dd></div>
          <div><dt>Szoftververzió</dt><dd>SZOFTVERVERZIÓ NEM IGAZOLHATÓ</dd></div>
        </dl>
        <p className="safety-copy">Az aktív küldetéshez és a szoftververzióhoz nincs olvasási szerződés; a felület nem vezet le értéket telemetriából vagy fájlnévből.</p>
      </section>

      <section className="live-operations-zone" aria-labelledby="robot-state-title">
        <p className="eyebrow">MÉRT ÁLLAPOT · IDŐHÖZ KÖTÖTT</p><h3 id="robot-state-title">Energia és helyzet</h3>
        <dl className="telemetry-details">
          <div><dt>Akkumulátor / power</dt><dd>{telemetryUsable ? metric(state.telemetry?.battery ?? null) : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Hely</dt><dd>{telemetryUsable ? state.telemetry?.location ?? "NEM ELLENŐRIZHETŐ" : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Mintavétel</dt><dd>{telemetryUsable ? state.telemetry?.capturedAt ?? "NEM ELLENŐRIZHETŐ" : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Front kamera</dt><dd>{cameraLabel}</dd></div>
        </dl>
        <p className="muted">Az elavult vagy hiányzó telemetria nem használható hely-, energia- vagy repülési állapotként.</p>
      </section>

      <section className="live-operations-zone" aria-labelledby="robot-safety-title">
        <p className="eyebrow">OPERÁTORI HATÁR · NINCS KÖZVETLEN VEZÉRLÉS</p><h3 id="robot-safety-title">Safety és vezérlési jogosultság</h3>
        <dl className="telemetry-details">
          <div><dt>Safety mód</dt><dd>{safetyMode}</dd></div>
          <div><dt>Max. magasság</dt><dd>{state.safety ? `${state.safety.maxAltitude} m` : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Max. sugár</dt><dd>{state.safety ? `${state.safety.maxRadius} m` : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Indítási akkuhatár</dt><dd>{state.safety ? `${state.safety.minBattery} %` : "NEM ELLENŐRIZHETŐ"}</dd></div>
        </dl>
        <p className="safety-copy">Közvetlen repülési parancs nem adható ki ebből a nézetből. Küldetési művelet kizárólag külön, SafetyGate által értékelt és explicit jóváhagyásos folyamatból indulhat.</p>
      </section>
    </div>
  </section>;
}
