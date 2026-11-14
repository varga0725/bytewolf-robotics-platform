import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { CameraPage } from "./CameraPage";
import { ChatPage } from "./ChatPage";
import { AnalyticsPage } from "./AnalyticsPage";
import { DeveloperPage } from "./DeveloperPage";
import { EventsPage } from "./EventsPage";
import { KnowledgePage } from "./KnowledgePage";
import { LiveOperationsPage } from "./LiveOperationsPage";
import { MemoryPage } from "./MemoryPage";
import { MissionPage } from "./MissionPage";
import { PerceptionPage } from "./PerceptionPage";
import { ReplayPage } from "./ReplayPage";
import { RobotsPage } from "./RobotsPage";
import { CognitiveRuntimePage } from "./CognitiveRuntimePage";
import { SettingsPage } from "./SettingsPage";
import { formatTelemetry, telemetryConnection, type TelemetrySnapshot } from "./telemetry";
import { WorldPage } from "./WorldPage";

type LoadState = "loading" | "ready" | "unavailable";
type View = "state" | "operations" | "robots" | "camera" | "perception" | "chat" | "mission" | "memory" | "knowledge" | "world" | "cognitive" | "events" | "analytics" | "replay" | "developer" | "settings";
type ConnectionState = "loading" | "ready" | "stale" | "unavailable";
type OperatorTelemetry = TelemetrySnapshot & { heading_deg: number | null };

const views: ReadonlyArray<{ id: View; label: string }> = [
  { id: "state", label: "Állapot" },
  { id: "camera", label: "Kamera" },
  { id: "operations", label: "Élő műveletek" },
  { id: "robots", label: "Robotok" },
  { id: "perception", label: "Percepció" },
  { id: "chat", label: "Beszélgetés" },
  { id: "mission", label: "Küldetés" },
  { id: "memory", label: "Memória" },
  { id: "knowledge", label: "Tudás" },
  { id: "world", label: "Világ" },
  { id: "cognitive", label: "Kognitív futtatókörnyezet" },
  { id: "events", label: "Események és naplók" },
  { id: "analytics", label: "Analitika" },
  { id: "replay", label: "Visszajátszás" },
  { id: "developer", label: "Fejlesztői diagnosztika" },
  { id: "settings", label: "Beállítások" },
];

const emptyTelemetry: OperatorTelemetry = {
  position: null,
  battery_percent: null,
  in_air: null,
  captured_at: null,
  heading_deg: null,
};

export function App() {
  const [activeView, setActiveView] = useState<View>("state");
  const [telemetry, setTelemetry] = useState<OperatorTelemetry>(emptyTelemetry);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [quickNavOpen, setQuickNavOpen] = useState(false);
  const [quickNavQuery, setQuickNavQuery] = useState("");
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const quickNavTriggerRef = useRef<HTMLButtonElement | null>(null);
  const quickNavSearchRef = useRef<HTMLInputElement | null>(null);
  const quickNavDialogRef = useRef<HTMLElement | null>(null);
  const quickNavReturnFocusRef = useRef<HTMLElement | null>(null);
  const restoreQuickNavFocusRef = useRef(false);

  useEffect(() => {
    let active = true;

    async function refreshTelemetry() {
      try {
        const response = await fetch("/api/v1/telemetry", { cache: "no-store" });
        if (!response.ok) throw new Error("Telemetry unavailable");
        const nextTelemetry = sanitizeTelemetry(await response.json());
        if (active) {
          setTelemetry(nextTelemetry);
          setLoadState("ready");
        }
      } catch {
        if (active) setLoadState("unavailable");
      }
    }

    void refreshTelemetry();
    const interval = window.setInterval(() => void refreshTelemetry(), 1000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    function handleQuickNavShortcut(event: globalThis.KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        if (isEditableTarget(event.target) || quickNavOpen) return;
        event.preventDefault();
        quickNavReturnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setQuickNavQuery("");
        setQuickNavOpen(true);
      }
    }

    document.addEventListener("keydown", handleQuickNavShortcut);
    return () => document.removeEventListener("keydown", handleQuickNavShortcut);
  }, [quickNavOpen]);

  useEffect(() => {
    if (quickNavOpen) quickNavSearchRef.current?.focus();
    if (!quickNavOpen && restoreQuickNavFocusRef.current) {
      restoreQuickNavFocusRef.current = false;
      (quickNavReturnFocusRef.current ?? quickNavTriggerRef.current)?.focus();
      quickNavReturnFocusRef.current = null;
    }
  }, [quickNavOpen]);

  const connection: { state: ConnectionState; label: string } =
    loadState === "loading"
      ? { state: "loading", label: "TELEMETRIA BETÖLTÉSE" }
      : loadState === "unavailable"
        ? { state: "unavailable", label: "TELEMETRIA NEM ELÉRHETŐ" }
        : telemetryConnection(telemetry.captured_at);
  const status = telemetryStatus(connection.state, telemetry.captured_at);
  const displayTelemetry = connection.state === "ready" || connection.state === "stale" ? telemetry : emptyTelemetry;
  const display = formatTelemetry(displayTelemetry);
  const integrity = telemetryIntegrity(displayTelemetry);
  const activeViewDetails = views.find((view) => view.id === activeView) ?? views[0];
  const quickNavResults = views.filter((view) => view.label.toLocaleLowerCase("hu").includes(quickNavQuery.trim().toLocaleLowerCase("hu")));
  const activeViewContent = activeView === "operations" ? <LiveOperationsPage />
    : activeView === "robots" ? <RobotsPage />
    : activeView === "camera" ? <CameraPage />
    : activeView === "perception" ? <PerceptionPage />
    : activeView === "chat" ? <ChatPage />
      : activeView === "mission" ? <MissionPage />
        : activeView === "memory" ? <MemoryPage />
          : activeView === "knowledge" ? <KnowledgePage />
          : activeView === "world" ? <WorldPage />
            : activeView === "cognitive" ? <CognitiveRuntimePage />
            : activeView === "events" ? <EventsPage />
            : activeView === "analytics" ? <AnalyticsPage />
            : activeView === "replay" ? <ReplayPage />
            : activeView === "developer" ? <DeveloperPage />
            : activeView === "settings" ? <SettingsPage />
            : <>
              <section className="safety-boundary" aria-labelledby="operating-context-heading">
                <p className="eyebrow">OPERÁTORI KONTEXTUS</p>
                <h2 id="operating-context-heading">Működési kontextus</h2>
                <p>Telemetria: {status.title}. {status.message}</p>
                <p>A felület csak olvasható: nincs közvetlen vezérlés. Szimulációs küldetés csak külön jóváhagyással indulhat.</p>
              </section>

              <section aria-labelledby="telemetry-heading">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">OPERÁTORI ÁLLAPOTKÉP</p>
                    <h2 id="telemetry-heading">Rendszerállapot</h2>
                  </div>
                  <p className={`telemetry-sample telemetry-sample--${connection.state}`} data-state={connection.state}>
                    {sampleLabel(connection.state)}
                  </p>
                </div>
                <div className="metric-grid">
                  <Metric label="REPÜLÉSI ÁLLAPOT" value={display.flight} />
                  <Metric label="RELATÍV MAGASSÁG" value={display.altitude} />
                  <Metric label="AKKUMULÁTOR" value={display.battery} />
                  <Metric label="POZÍCIÓ" value={display.position} />
                  <Metric label="IRÁNYTARTÁS" value={formatHeading(displayTelemetry.heading_deg)} />
                </div>
              </section>

              <section className={`telemetry-integrity telemetry-integrity--${connection.state}`} aria-labelledby="integrity-heading">
                <div>
                  <p className="eyebrow">BIZONYÍTÉK-ELSŐ ADATMINŐSÉG</p>
                  <h2 id="integrity-heading">Telemetria integritása</h2>
                </div>
                <dl className="telemetry-details">
                  <div><dt>Mintavételi állapot</dt><dd>{sampleLabel(connection.state)}</dd></div>
                  <div><dt>Ellenőrzött mezők</dt><dd>{integrity.validFields} / 5 adatmező ellenőrizve</dd></div>
                  <div><dt>Rögzítés</dt><dd>{status.capturedAt ?? "NEM ELÉRHETŐ"}</dd></div>
                  <div><dt>Minta kora</dt><dd>{connection.state === "ready" || connection.state === "stale" ? telemetryAge(status.capturedAt) : "NEM ELÉRHETŐ"}</dd></div>
                  <div><dt>Pozíció fix</dt><dd>{integrity.position ? "ELLENŐRIZVE" : "NEM ELÉRHETŐ"}</dd></div>
                </dl>
                <p className="safety-copy">A kijelző csak olvasható. A hiányzó, hibás vagy időben ellentmondásos adatot nem becsüli meg.</p>
              </section>
            </>;

  function selectView(view: View, focusTab = false) {
    if (focusTab) {
      const viewIndex = views.findIndex((candidate) => candidate.id === view);
      tabRefs.current[viewIndex]?.focus();
    }
    setActiveView(view);
  }

  function openQuickNav() {
    quickNavReturnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setQuickNavQuery("");
    setQuickNavOpen(true);
  }

  function closeQuickNav(restoreFocus = true) {
    restoreQuickNavFocusRef.current = restoreFocus;
    setQuickNavOpen(false);
  }

  function selectQuickNavView(view: View) {
    selectView(view, true);
    closeQuickNav(false);
  }

  function handleQuickNavKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeQuickNav();
      return;
    }

    if (event.key === "Enter" && quickNavResults[0]) {
      event.preventDefault();
      selectQuickNavView(quickNavResults[0].id);
    }
  }

  function handleQuickNavDialogKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeQuickNav();
      return;
    }

    if (event.key !== "Tab") return;
    const focusable = quickNavDialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, viewIndex: number) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (viewIndex + 1) % views.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (viewIndex - 1 + views.length) % views.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = views.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    selectView(views[nextIndex].id, true);
  }

  return (
    <main className="control-room">
      <header className="topbar">
        <div>
          <p className="eyebrow">BYTEWOLF ROBOTICS</p>
          <h1>Control Room</h1>
        </div>
        <p className={`connection connection--${connection.state}`} aria-live="polite">
          {connection.label}
        </p>
        <button
          ref={quickNavTriggerRef}
          type="button"
          aria-haspopup="dialog"
          aria-expanded={quickNavOpen}
          aria-controls="quick-view-navigation"
          aria-label="Gyors navigáció megnyitása"
          onClick={openQuickNav}
        >
          Gyorsnav ⌘K
        </button>
      </header>

      <section className={`operation-status-strip operation-status-strip--${connection.state}`} role="region" aria-label="Operátori rendszerállapot">
        <div>
          <p className="eyebrow">PLATFORM ÁLLAPOT</p>
          <strong>{connection.label}</strong>
          <span>Telemetria-alapú kapcsolati jelzés</span>
        </div>
        <div>
          <p className="eyebrow">BIZTONSÁGI MÓD</p>
          <strong>Jóváhagyás-köteles</strong>
          <span>Nincs közvetlen vezérlés</span>
        </div>
        <div>
          <p className="eyebrow">KÖRNYEZET</p>
          <strong>Szimuláció</strong>
          <span>Operátori megfigyelés</span>
        </div>
        <div>
          <p className="eyebrow">KAPCSOLÓDÓ BODY</p>
          <strong>1 SZIMULÁLT TEST</strong>
          <span>Az egyetlen konfigurált szimulációs body</span>
        </div>
        <div>
          <p className="eyebrow">AKTÍV KÜLDETÉS</p>
          <strong>NEM ELLENŐRIZHETŐ</strong>
          <span>Nincs ellenőrzött aktív küldetésadat</span>
        </div>
        <div>
          <p className="eyebrow">RIASZTÁSOK</p>
          <strong>NEM ELLENŐRIZHETŐ</strong>
          <span>Nincs ellenőrzött riasztási feed</span>
        </div>
        <div>
          <p className="eyebrow">LEGUTÓBBI MINTA</p>
          <strong>{status.title}</strong>
          <span>{status.capturedAt ?? "NEM ELLENŐRIZHETŐ"}</span>
        </div>
      </section>

      <section
        className="safety-boundary"
        role="region"
        aria-label="Élő telemetria állapot"
      >
        <p className="eyebrow">ÉLŐ ADATMINŐSÉG</p>
        <h2>{status.title}</h2>
        <p>{status.message}</p>
        {status.capturedAt !== null && <p>Rögzítés ideje: {status.capturedAt}</p>}
      </section>

      <section className="safety-boundary" aria-label="Biztonsági határ">
        A felület állapotot mutat, küldetést tervez és külön jóváhagyást kér. Nem ad közvetlen
        PX4-, MAVSDK- vagy aktuátorvezérlést.
      </section>

      <nav className="view-navigation" aria-label="Control Room nézetek" role="tablist" aria-orientation="horizontal">
        {views.map((view, index) => (
          <button
            key={view.id}
            ref={(element) => { tabRefs.current[index] = element; }}
            id={`view-tab-${view.id}`}
            className={activeView === view.id ? "nav-button active" : "nav-button"}
            type="button"
            role="tab"
            aria-controls={`view-panel-${view.id}`}
            aria-selected={activeView === view.id}
            tabIndex={activeView === view.id ? 0 : -1}
            onClick={() => selectView(view.id)}
            onKeyDown={(event) => handleTabKeyDown(event, index)}
          >
            {view.label}
          </button>
        ))}
      </nav>
      <p className="eyebrow" aria-live="polite" aria-atomic="true">Aktív nézet: {activeViewDetails.label}</p>

      {quickNavOpen && (
        <section
          id="quick-view-navigation"
          ref={quickNavDialogRef}
          className="content-card"
          role="dialog"
          aria-modal="true"
          aria-labelledby="quick-nav-heading"
          onKeyDown={handleQuickNavDialogKeyDown}
        >
          <div className="section-heading">
            <div>
              <p className="eyebrow">OPERÁTORI GYORSNAV</p>
              <h2 id="quick-nav-heading">Gyors nézetváltó</h2>
            </div>
            <button type="button" onClick={() => closeQuickNav()}>Bezárás</button>
          </div>
          <p className="muted">Csak nézetváltás — ez a panel nem indít küldetést és nem vezérli a robotot.</p>
          <label className="field-label" htmlFor="quick-nav-search">
            Nézet keresése
            <input
              ref={quickNavSearchRef}
              id="quick-nav-search"
              role="combobox"
              aria-autocomplete="list"
              aria-controls="quick-nav-results"
              aria-expanded="true"
              value={quickNavQuery}
              onChange={(event) => setQuickNavQuery(event.target.value)}
              onKeyDown={handleQuickNavKeyDown}
            />
          </label>
          <div id="quick-nav-results" role="listbox" aria-label="Elérhető nézetek">
            {quickNavResults.length > 0 ? quickNavResults.map((view) => (
              <button key={view.id} type="button" role="option" aria-selected={view.id === activeView} onClick={() => selectQuickNavView(view.id)}>
                {view.label}
              </button>
            )) : <p className="muted" role="status">Nincs egyező nézet.</p>}
          </div>
        </section>
      )}

      {views.map((view) => (
        <section
          key={view.id}
          id={`view-panel-${view.id}`}
          role="tabpanel"
          aria-labelledby={`view-tab-${view.id}`}
          tabIndex={-1}
          hidden={activeView !== view.id}
        >
          {activeView === view.id ? activeViewContent : null}
        </section>
      ))}
    </main>
  );
}

function telemetryStatus(
  state: ConnectionState,
  capturedAt: string | null,
): { title: string; message: string; capturedAt: string | null } {
  if (state === "loading") {
    return {
      title: "Telemetria betöltése",
      message: "Az élő állapot betöltése folyamatban; az értékek még nem tekinthetők aktuálisnak.",
      capturedAt: null,
    };
  }

  if (state === "unavailable") {
    return {
      title: "Telemetria nem elérhető",
      message: "Az állapotértékek nem tekinthetők aktuálisnak.",
      capturedAt: null,
    };
  }

  const age = telemetryAge(capturedAt);
  if (state === "stale") {
    return {
      title: "Telemetria elavult",
      message: `Az értékek korábbi mérést mutatnak, nem aktuálisak. Rögzítés óta: ${age}.`,
      capturedAt,
    };
  }

  return {
    title: "Telemetria kapcsolódva",
    message: `Az élő állapot friss. Rögzítés óta: ${age}.`,
    capturedAt,
  };
}

function telemetryAge(capturedAt: string | null): string {
  const capturedTime = capturedAt === null ? Number.NaN : Date.parse(capturedAt);
  if (Number.isNaN(capturedTime)) return "ismeretlen ideje";

  const seconds = Math.max(0, Math.floor((Date.now() - capturedTime) / 1000));
  return `${seconds} másodperce`;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="metric-card">
      <p>{label}</p>
      <strong>{value}</strong>
    </article>
  );
}

function sanitizeTelemetry(value: unknown): OperatorTelemetry {
  if (!isRecord(value)) return emptyTelemetry;

  const position = sanitizePosition(value.position);
  const battery = boundedNumber(value.battery_percent, 0, 100);
  const heading = boundedNumber(value.heading_deg, -360, 360);
  const capturedAt = safeCaptureTime(value.captured_at) ? value.captured_at : null;

  return {
    position,
    battery_percent: battery,
    in_air: typeof value.in_air === "boolean" ? value.in_air : null,
    captured_at: capturedAt,
    heading_deg: heading,
  };
}

function sanitizePosition(value: unknown): TelemetrySnapshot["position"] {
  if (!isRecord(value)) return null;
  const latitude = boundedNumber(value.latitude_deg, -90, 90);
  const longitude = boundedNumber(value.longitude_deg, -180, 180);
  const absoluteAltitude = finiteNumber(value.absolute_altitude_m);
  const relativeAltitude = value.relative_altitude_m === null ? null : finiteNumber(value.relative_altitude_m);
  if (latitude === null || longitude === null || absoluteAltitude === null || relativeAltitude === null && value.relative_altitude_m !== null) return null;

  return { latitude_deg: latitude, longitude_deg: longitude, absolute_altitude_m: absoluteAltitude, relative_altitude_m: relativeAltitude };
}

function telemetryIntegrity(snapshot: OperatorTelemetry): { validFields: number; position: boolean } {
  const position = snapshot.position !== null;
  return {
    validFields: [snapshot.in_air !== null, snapshot.battery_percent !== null, position, snapshot.heading_deg !== null, snapshot.captured_at !== null]
      .filter(Boolean).length,
    position,
  };
}

function sampleLabel(state: ConnectionState): string {
  if (state === "ready") return "ÉLŐ MINTA";
  if (state === "stale") return "ELAVULT MINTA";
  if (state === "loading") return "MINTA BETÖLTÉSE";
  return "NEM ELLENŐRIZHETŐ";
}

function formatHeading(heading: number | null): string {
  if (heading === null) return "NEM ELÉRHETŐ";
  const normalized = ((heading % 360) + 360) % 360;
  return `${normalized.toFixed(1).replace(".", ",")}°`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (
    target.isContentEditable || target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT"
  );
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function boundedNumber(value: unknown, minimum: number, maximum: number): number | null {
  const number = finiteNumber(value);
  return number !== null && number >= minimum && number <= maximum ? number : null;
}

function safeCaptureTime(value: unknown): value is string {
  if (typeof value !== "string" || value.trim() === "") return false;
  const capturedTime = Date.parse(value);
  // Browser and telemetry-host clocks can differ by a few milliseconds even
  // when both use NTP. Reject material future data, but do not turn harmless
  // transport/clock skew into a permanent "unavailable" dashboard.
  return Number.isFinite(capturedTime) && capturedTime <= Date.now() + 5_000;
}
