import { FormEvent, useEffect, useRef, useState } from "react";

import { post } from "./api";

type ChatReply = {
  text: string;
  status: string;
  plan_id: string | null;
  plan_hash?: string | null;
  approval_required: boolean;
};

type ApprovalTokenReply = {
  approval_token: string;
  plan_hash: string;
  expires_in_s: number;
};

type Message = { role: "operator" | "assistant"; text: string };
type Lifecycle = "ready" | "drafting" | "approval" | "submitted" | "completed" | "failed" | "cancelled" | "error";

const lifecycleLabel: Record<Lifecycle, string> = {
  ready: "Készen áll",
  drafting: "Terv készül",
  approval: "Jóváhagyásra vár",
  submitted: "Küldetés elküldve",
  completed: "Lezárt: sikeres",
  failed: "Lezárt: sikertelen",
  cancelled: "Lezárt: visszavonva",
  error: "Művelet sikertelen",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "ismeretlen hiba";
}

type ChatPageProps = {
  commandsEnabled: boolean;
};

export function ChatPage({ commandsEnabled }: ChatPageProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [pendingPlan, setPendingPlan] = useState<string | null>(null);
  const [planHash, setPlanHash] = useState<string | null>(null);
  const [approvalToken, setApprovalToken] = useState<string | null>(null);
  const [confirmStep, setConfirmStep] = useState<0 | 1>(0);
  const [trackedPlan, setTrackedPlan] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lifecycle, setLifecycle] = useState<Lifecycle>("ready");
  const [status, setStatus] = useState("Beszélgetésre kész.");
  const statusTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (statusTimer.current !== null) window.clearInterval(statusTimer.current);
  }, []);

  function appendAssistant(text: string) {
    setMessages((current) => [...current, { role: "assistant", text }]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || busy || pendingPlan) return;

    setMessages((current) => [...current, { role: "operator", text: trimmed }]);
    setText("");
    setBusy(true);
    setLifecycle("drafting");
    setStatus("A kérésből terv készül; ez még nem indít küldetést.");
    try {
      const reply = await post<ChatReply>("/api/v1/chat", { text: trimmed });
      appendAssistant(reply.text);
      if (reply.approval_required && reply.plan_id) {
        setPendingPlan(reply.plan_id);
        setPlanHash(reply.plan_hash ?? null);
        setApprovalToken(null);
        setConfirmStep(0);
        setTrackedPlan(reply.plan_id);
        setLifecycle("approval");
        setStatus("A terv csak a kifejezett jóváhagyásodra küldhető el a szimulációba.");
      } else {
        setLifecycle("ready");
        setStatus("A válasz elkészült; nem vár jóváhagyásra.");
      }
    } catch (error) {
      setLifecycle("error");
      setStatus(`A terv nem készült el: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function prepareConfirmation() {
    if (!pendingPlan || busy) return;
    setBusy(true);
    setStatus("Szerver jóváhagyási token igénylése…");
    try {
      const payload = await post<ApprovalTokenReply>("/api/v1/plans/approval-token", { plan_id: pendingPlan });
      setApprovalToken(payload.approval_token);
      setPlanHash(payload.plan_hash);
      setConfirmStep(1);
      setStatus("Ellenőrizd a terv-hash értéket, majd erősítsd meg a jóváhagyást.");
    } catch (error) {
      setLifecycle("error");
      setStatus(`A jóváhagyási token nem kérhető: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function decide(action: "approve" | "cancel") {
    if (!pendingPlan || busy) return;
    const planId = pendingPlan;
    if (action === "approve" && confirmStep === 0) {
      await prepareConfirmation();
      return;
    }
    setBusy(true);
    setStatus(action === "approve" ? "A jóváhagyás elküldése folyamatban van." : "A terv visszavonása folyamatban van.");
    try {
      const reply = await post<ChatReply>(
        `/api/v1/plans/${action}`,
        action === "approve"
          ? { plan_id: planId, approval_token: approvalToken ?? "" }
          : { plan_id: planId },
      );
      appendAssistant(reply.text);
      setPendingPlan(null);
      setPlanHash(null);
      setApprovalToken(null);
      setConfirmStep(0);
      if (action === "cancel") {
        setLifecycle("cancelled");
        setStatus("A tervet kifejezetten visszavontad; küldetés nem indult.");
        return;
      }

      setLifecycle("submitted");
      setStatus("A jóváhagyott küldetés elküldve; a terminális visszajelzésre várunk.");
      monitor(planId);
    } catch (error) {
      setLifecycle("error");
      setStatus(`A ${action === "approve" ? "jóváhagyás" : "visszavonás"} nem sikerült: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  }

  function monitor(planId: string) {
    if (statusTimer.current !== null) window.clearInterval(statusTimer.current);

    const refresh = () => {
      void fetch(`/api/v1/plans/${encodeURIComponent(planId)}/status`, { cache: "no-store" })
        .then(async (response) => ({ response, data: (await response.json()) as { status?: string; message?: string } }))
        .then(({ response, data }) => {
          if (!response.ok || !data.status) throw new Error("A küldetési állapot nem olvasható.");
          if (data.status !== "completed" && data.status !== "failed") return;
          if (statusTimer.current !== null) window.clearInterval(statusTimer.current);
          statusTimer.current = null;
          const terminal = data.status === "completed" ? "completed" : "failed";
          setLifecycle(terminal);
          setStatus(data.message ?? (terminal === "completed" ? "A küldetés sikeresen lezárult." : "A küldetés sikertelenül lezárult."));
        })
        .catch(() => {
          if (statusTimer.current !== null) window.clearInterval(statusTimer.current);
          statusTimer.current = null;
          setLifecycle("error");
          setStatus("A végrehajtási állapot nem ellenőrizhető. Ellenőrizd a Visszajátszás nézet audit-artifactját.");
        });
    };

    refresh();
    statusTimer.current = window.setInterval(refresh, 1_000);
  }

  const inputLocked = busy || pendingPlan !== null || lifecycle === "submitted";

  return (
    <section className="content-card" aria-labelledby="chat-title">
      <div className="section-heading"><div><p className="eyebrow">KÜLDETÉS-ASSZISZTENS</p><h2 id="chat-title">Beszélgetés</h2></div></div>
      <p id="chat-guidance" className="safety-copy">
        {commandsEnabled
          ? "A Cognitive Runtime csak javaslatot készít. A kérés először tervvé válik, a SafetyGate ellenőrzi, és jóváhagyás nélkül nem indul szimulációs küldetés."
          : "Ground Control Room mód: a repülési tervezés és jóváhagyás nem érhető el. A nem repülési beszélgetés továbbra is működik."}
      </p>
      <p className="muted" role="status" aria-atomic="true"><strong>Állapot: {lifecycleLabel[lifecycle]}</strong><br />{status}{trackedPlan && lifecycle !== "ready" && <><br /><span>Kapcsolódó terv: {trackedPlan}</span></>}</p>
      <div className="message-list" role="log" aria-label="Küldetési beszélgetés" aria-live="polite" aria-relevant="additions">
        {messages.length === 0 ? (
          <p className="muted">
            {commandsEnabled
              ? "Írj például egy rövid, magas szintű küldetési célt."
              : "Írj egy rövid üzenetet — a nem repülési válaszok Ground módban is működnek."}
          </p>
        ) : messages.map((message, index) => <p className={`message message--${message.role}`} key={`${message.role}-${index}`} aria-label={message.role === "operator" ? "Operátor üzenete" : "Asszisztens üzenete"}>{message.text}</p>)}
      </div>
      {commandsEnabled && pendingPlan && (
        <div className="approval-panel" aria-labelledby="approval-title" aria-describedby="approval-boundary approval-id approval-hash">
          <strong id="approval-title">{confirmStep === 0 ? "Jóváhagyásra vár" : "Terv-hash megerősítése"}</strong>
          <p id="approval-id">Tervazonosító: {pendingPlan}</p>
          {planHash && <p id="approval-hash"><strong>Terv SHA-256:</strong> <code>{planHash}</code></p>}
          <p id="approval-boundary">Ellenőrzési határ: az asszisztens csak javaslatot készített.</p>
          <p>
            {confirmStep === 0
              ? "Ez a terv csak ebben a böngésző-sessionben érvényes. A következő lépésben a szerver rövid élettartamú jóváhagyási tokent ad ki."
              : "A jóváhagyás csak akkor indítható, ha a fenti hash megegyezik a szerver által kiadott tokennel összekötött tervvel."}
          </p>
          <button type="button" className="primary" onClick={() => void decide("approve")} disabled={busy}>
            {confirmStep === 0 ? "Tovább a terv-hash megerősítéséhez" : "Kifejezett jóváhagyás és indítás"}
          </button>
          <button type="button" onClick={() => void decide("cancel")} disabled={busy}>Terv visszavonása</button>
        </div>
      )}
      <form className="composer" onSubmit={submit}>
        <label htmlFor="chat-prompt">{commandsEnabled ? "Küldetési kérés" : "Üzenet"}</label>
        <textarea id="chat-prompt" aria-describedby="chat-guidance" value={text} onChange={(event) => setText(event.target.value)} maxLength={2000} rows={3} disabled={inputLocked} />
        <button className="primary" disabled={inputLocked || !text.trim()}>
          {lifecycle === "drafting" ? "Terv készül…" : commandsEnabled ? "Terv készítése" : "Küldés"}
        </button>
      </form>
      {!commandsEnabled && <p className="muted" role="note">Repülési szándék SITL konzol módban érhető el, ha a szerveren a parancsút szándékosan engedélyezett.</p>}
    </section>
  );
}
