import { useEffect, useState } from "react";

import { api } from "./api";

type TelemetryState = "fresh" | "stale" | "unavailable";
type SafetyProfile = { maxAltitude: number; maxRadius: number; minBattery: number };
type SettingsState = {
  safety: SafetyProfile | null;
  telemetryState: TelemetryState;
  checkedAt: string | null;
};

const initialState: SettingsState = { safety: null, telemetryState: "unavailable", checkedAt: null };

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function validTimestamp(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function readSafety(value: unknown): SafetyProfile | null {
  if (!record(value)) return null;
  const maxAltitude = value.max_altitude_m;
  const maxRadius = value.max_radius_m;
  const minBattery = value.minimum_battery_percent_to_start;
  if (!finite(maxAltitude) || !finite(maxRadius) || !finite(minBattery) || maxAltitude <= 0 || maxRadius <= 0 || minBattery < 0 || minBattery > 100) return null;
  return { maxAltitude, maxRadius, minBattery };
}

function readTelemetryState(value: unknown): TelemetryState {
  if (!record(value) || !validTimestamp(value.captured_at)) return "unavailable";
  return Date.now() - Date.parse(value.captured_at) > 10_000 ? "stale" : "fresh";
}

function telemetryLabel(state: TelemetryState): string {
  return state === "fresh" ? "TELEMETRIA FRISS" : state === "stale" ? "TELEMETRIA ELAVULT" : "TELEMETRIA NEM ELLENŐRIZHETŐ";
}

function checkedAt(value: string | null): string {
  return value ? new Date(value).toLocaleTimeString("hu-HU") : "NEM ELLENŐRIZHETŐ";
}

export function SettingsPage() {
  const [state, setState] = useState<SettingsState>(initialState);

  useEffect(() => {
    let active = true;
    void Promise.allSettled([api<unknown>("/api/v1/safety-envelope"), api<unknown>("/api/v1/telemetry")]).then(([safetyResult, telemetryResult]) => {
      if (!active) return;
      setState({
        safety: safetyResult.status === "fulfilled" ? readSafety(safetyResult.value) : null,
        telemetryState: telemetryResult.status === "fulfilled" ? readTelemetryState(telemetryResult.value) : "unavailable",
        checkedAt: new Date().toISOString(),
      });
    });
    return () => { active = false; };
  }, []);

  const safetyReadable = state.safety !== null;
  return <section className="content-card" aria-labelledby="settings-page-title">
    <div className="section-heading">
      <div><p className="eyebrow">RENDSZERKONFIGURÁCIÓ · CSAK OLVASHATÓ</p><h2 id="settings-page-title">Rendszerbeállítások</h2></div>
      <p className={`telemetry-sample telemetry-sample--${state.telemetryState}`}>{safetyReadable ? "SAFETY PROFIL OLVASHATÓ" : "SAFETY PROFIL NEM ELLENŐRIZHETŐ"}</p>
    </div>
    <p className="muted" role="status">Utolsó szerződés-ellenőrzés: {checkedAt(state.checkedAt)} · {telemetryLabel(state.telemetryState)}.</p>

    <div className="live-operations-grid">
      <section className="live-operations-zone" aria-label="Szerver által rögzített safety profil">
        <p className="eyebrow">01 · SZERVEROLDALI, RÖGZÍTETT</p><h3>Szerver által rögzített safety profil</h3>
        <dl className="telemetry-details">
          <div><dt>Profil állapota</dt><dd>{safetyReadable ? "ELLENŐRIZHETŐ" : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Max. magasság</dt><dd>{state.safety ? `${state.safety.maxAltitude} m` : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Max. sugár</dt><dd>{state.safety ? `${state.safety.maxRadius} m` : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Indítási akkuhatár</dt><dd>{state.safety ? `${state.safety.minBattery} %` : "NEM ELLENŐRIZHETŐ"}</dd></div>
        </dl>
        <p className="safety-copy">A safety korlátok nem helyi beállítások: ezt a felületet a szerver szolgáltatja, a SafetyGate pedig küldetésenként külön értékeli.</p>
      </section>

      <section className="live-operations-zone" aria-label="Helyi megjelenítési preferenciák">
        <p className="eyebrow">02 · HELYI, NEM OPERATÍV</p><h3>Helyi megjelenítési preferenciák</h3>
        <dl className="telemetry-details">
          <div><dt>Hatókör</dt><dd>Csak ezen a böngészőn</dd></div>
          <div><dt>Vizuális rendszer</dt><dd>OPERÁTORI SÖTÉT TÉMA</dd></div>
          <div><dt>Adatsűrűség</dt><dd>RESZPONZÍV, OLVASHATÓ</dd></div>
          <div><dt>Vezérlési jogosultság</dt><dd>NINCS HATÁSA</dd></div>
        </dl>
        <p className="muted">Ezek a kijelzési alapelvek nem írnak safety-profilt, nem változtatnak küldetést és nem módosítják a szimulációt.</p>
      </section>

      <section className="live-operations-zone" aria-label="Nem konfigurálható vezérlési felületek">
        <p className="eyebrow">03 · OPERÁTORI HATÁR</p><h3>Nem konfigurálható vezérlési felületek</h3>
        <dl className="telemetry-details">
          <div><dt>Közvetlen repülési parancs</dt><dd>NEM ELÉRHETŐ</dd></div>
          <div><dt>Safety limit módosítása</dt><dd>NEM ELÉRHETŐ</dd></div>
          <div><dt>Autonóm indítás</dt><dd>NEM ELÉRHETŐ</dd></div>
          <div><dt>Telemetria érvényesítése</dt><dd>{telemetryLabel(state.telemetryState)}</dd></div>
        </dl>
        <p className="safety-copy">Küldetési művelet csak külön tervezési, SafetyGate-ellenőrzési és explicit jóváhagyási folyamatból indulhat. Ez a nézet semmilyen végrehajtási API-t nem hív.</p>
      </section>
    </div>
  </section>;
}
