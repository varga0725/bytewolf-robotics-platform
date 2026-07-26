import { useEffect, useMemo, useState } from "react";

type CameraSensor = "front" | "down";

const cameraOptions: ReadonlyArray<{ id: CameraSensor; label: string; imageLabel: string }> = [
  { id: "front", label: "Elülső kamera", imageLabel: "Elülső kamera élőképe" },
  { id: "down", label: "Alsó kamera", imageLabel: "Alsó kamera élőképe" },
];

type Detection = { label?: string; confidence?: number; bbox?: { x?: number; y?: number; width?: number; height?: number } };
type DetectionDocument = {
  validity?: string;
  captured_at?: string;
  max_age_s?: number;
  frame?: { width?: number; height?: number; frame_id?: string };
  detections?: Detection[];
};
type ValidFrame = { width: number; height: number };
type ValidDetection = { label: string; confidence: number; bbox: { x: number; y: number; width: number; height: number } };
type EvidenceState =
  | { kind: "loading" }
  | { kind: "unavailable" }
  | { kind: "invalid"; message: string }
  | { kind: "stale"; capturedAt: string; maxAgeSeconds: number }
  | { kind: "fresh"; capturedAt: string; maxAgeSeconds: number; frameId?: string; detections: ValidDetection[]; frame: ValidFrame };

const isFiniteNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function validFrame(frame: DetectionDocument["frame"]): ValidFrame | null {
  if (!isFiniteNumber(frame?.width) || !isFiniteNumber(frame?.height) || frame.width <= 0 || frame.height <= 0) return null;
  return { width: frame.width, height: frame.height };
}

function validDetection(detection: Detection, frame: ValidFrame): ValidDetection | null {
  const { x, y, width, height } = detection.bbox ?? {};
  if (
    typeof detection.label !== "string" || !detection.label.trim()
    || !isFiniteNumber(detection.confidence) || detection.confidence < 0 || detection.confidence > 1
    || ![x, y, width, height].every(isFiniteNumber)
    || x === undefined || y === undefined || width === undefined || height === undefined
    || x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > frame.width || y + height > frame.height
  ) return null;
  return { label: detection.label.trim(), confidence: detection.confidence, bbox: { x, y, width, height } };
}

function assessEvidence(document: DetectionDocument, now = Date.now()): EvidenceState {
  if (document.validity !== "valid") return { kind: "invalid", message: "A forrás nem jelölte érvényesnek az észlelést." };
  const frame = validFrame(document.frame);
  if (!frame || !Array.isArray(document.detections)) return { kind: "invalid", message: "Hiányos vagy hibás észlelési szerződés." };
  if (typeof document.captured_at !== "string" || !isFiniteNumber(document.max_age_s) || document.max_age_s <= 0) {
    return { kind: "invalid", message: "Hiányzik vagy hibás a bizonyíték frissességi adata." };
  }
  const capturedMs = Date.parse(document.captured_at);
  if (Number.isNaN(capturedMs) || capturedMs - now > 1000) return { kind: "invalid", message: "A bizonyíték időbélyege nem értelmezhető." };
  if (now - capturedMs > document.max_age_s * 1000) return { kind: "stale", capturedAt: document.captured_at, maxAgeSeconds: document.max_age_s };

  const detections = document.detections.map((detection) => validDetection(detection, frame));
  if (detections.some((detection) => detection === null)) {
    return { kind: "invalid", message: "Legalább egy észlelési rekord sérti a képkocka-szerződést; semmit sem rajzolunk rá." };
  }
  const verifiedDetections = detections.filter((detection): detection is ValidDetection => detection !== null);
  return { kind: "fresh", capturedAt: document.captured_at, maxAgeSeconds: document.max_age_s, frameId: document.frame?.frame_id, detections: verifiedDetections, frame };
}

function EvidenceSummary({ evidence }: { evidence: EvidenceState }) {
  if (evidence.kind === "loading") return <p role="status" aria-live="polite">Bizonyíték betöltése…</p>;
  if (evidence.kind === "unavailable") return <p role="status" aria-live="polite">Az észlelési bizonyíték nem elérhető; az élőképet nem értelmezzük objektumészlelésként.</p>;
  if (evidence.kind === "invalid") return <p role="status" aria-live="polite">Az észlelési bizonyíték érvénytelen: {evidence.message}</p>;
  if (evidence.kind === "stale") return <p role="status" aria-live="polite">Elavult bizonyíték: <time dateTime={evidence.capturedAt}>{evidence.capturedAt}</time> (legfeljebb {evidence.maxAgeSeconds} mp-ig volt érvényes). Az észlelések rejtve maradnak.</p>;
  return <p role="status" aria-live="polite">Friss, érvényes bizonyíték: {evidence.detections.length} észlelés.</p>;
}

export function CameraPage() {
  const [selectedCamera, setSelectedCamera] = useState<CameraSensor>("front");
  const [evidence, setEvidence] = useState<EvidenceState>({ kind: "loading" });
  const camera = useMemo(() => cameraOptions.find((option) => option.id === selectedCamera) ?? cameraOptions[0], [selectedCamera]);

  useEffect(() => {
    let active = true;
    async function refreshDetections() {
      try {
        const response = await fetch(`/api/v1/cameras/${selectedCamera}/detections`, { cache: "no-store" });
        if (!response.ok) throw new Error("detections unavailable");
        const next = (await response.json()) as DetectionDocument;
        if (active) setEvidence(assessEvidence(next));
      } catch {
        if (active) setEvidence({ kind: "unavailable" });
      }
    }
    setEvidence({ kind: "loading" });
    void refreshDetections();
    const interval = window.setInterval(() => void refreshDetections(), 500);
    return () => { active = false; window.clearInterval(interval); };
  }, [selectedCamera]);

  const freshEvidence = evidence.kind === "fresh" ? evidence : null;
  return <section className="content-card camera-page" aria-labelledby="camera-page-title">
    <div className="section-heading"><div><p className="eyebrow">ÉLŐ MEGFIGYELÉS · CSAK OLVASHATÓ</p><h2 id="camera-page-title">Kamera</h2></div>
      <label className="field-label" htmlFor="camera-sensor">Kamera forrása<select id="camera-sensor" value={selectedCamera} onChange={(event) => setSelectedCamera(event.target.value as CameraSensor)}>{cameraOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
    </div>
    <div className="camera-stream-frame">
      <div className="camera-image-wrap"><img src={`/api/v1/cameras/${camera.id}/stream`} alt={camera.imageLabel} />
        {freshEvidence && <svg className="detection-overlay" viewBox={`0 0 ${freshEvidence.frame.width} ${freshEvidence.frame.height}`} aria-hidden="true">{freshEvidence.detections.map((detection, index) => { const { x, y, width, height } = detection.bbox; return <g key={`${detection.label}-${index}`}><rect x={x} y={y} width={width} height={height} /><text x={x} y={Math.max(16, y - 4)}>{`${detection.label} ${Math.round(detection.confidence * 100)}%`}</text></g>; })}</svg>}
      </div>
      <EvidenceSummary evidence={evidence} />
      <p>Forrás: {camera.label} · észlelési szerződés: <code>/api/v1/cameras/{camera.id}/detections</code></p>
      {freshEvidence && <><p>Rögzítve: <time dateTime={freshEvidence.capturedAt}>{freshEvidence.capturedAt}</time> · érvényes {freshEvidence.maxAgeSeconds} mp-ig{freshEvidence.frameId ? ` · képkocka: ${freshEvidence.frameId}` : ""}</p>
        <ul className="data-list" aria-label="Érvényes objektumészlelések">{freshEvidence.detections.length ? freshEvidence.detections.map((detection, index) => <li key={`${detection.label}-${index}`}><strong>{detection.label}</strong><span>Bizonyossági jelzés: {Math.round(detection.confidence * 100)}% (nem kalibrált valószínűség)</span></li>) : <li>Nincs észlelés ebben az érvényes képkockában. Ez nem igazolja, hogy a látómező akadálymentes.</li>}</ul>
      </>}
    </div>
  </section>;
}
